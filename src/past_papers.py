"""PDF-grounded past-paper stage (pure helpers + Gemini/network).

Two-pass, grounded-not-recalled: for each fetchable paper PDF in the source
registry we (1) generate candidate citations by reading the PDF as a ``Part``, then
(2) independently verify each against the SAME PDF and keep only the confirmed ones.
A ``VerifiedPaper`` is ONLY ever built from a PDF fetched this run, and its ``url``
is set from the registry — never by the model. ``resources[]`` signposting is filled
for every board; ``verified[]`` only where a lawful paper is fetchable, else we
degrade to resources-only. ``build_past_papers`` never raises.

The pure helpers (no genai / no network) hold the security-critical assembly so the
offline smoke can exercise them without a key (see ``_smoke_past_papers.py``).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from google.genai import types

import helpers
from config import CONFIG
from schemas import CandidateCitations, PastPapers, VerificationReport, VerifiedPaper
from sources import PaperSource, ResolvedSources, resolve_sources

_PDF_ACCEPT = ("application/pdf", "application/octet-stream")


# ---------------------------------------------------------------------------
# pure helpers (no genai / no network; offline-testable)
# ---------------------------------------------------------------------------

def safe_http_url(url: str, allowlist: "tuple[str, ...]") -> str:
    """Return ``url`` iff it is http(s) AND its host is in ``allowlist``, else ''.
    This is the single gate through which a rendered paper link can pass — so a
    ``javascript:`` scheme or an off-allowlist host can never reach an href."""
    try:
        p = urlparse(url or "")
    except Exception:
        return ""
    host = (p.hostname or "").lower()
    if p.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in allowlist):
        return url
    return ""


def sanitize_summary(text: str) -> str:
    """Strip any markup + collapse whitespace so a model/PDF-sourced summary is safe
    on every render path (defence-in-depth alongside the textContent render)."""
    return " ".join(helpers._strip_tags(text or "").split())


def confirmed_to_verified(candidates, verdicts, paper: PaperSource,
                          allowlist: "tuple[str, ...]") -> "list[VerifiedPaper]":
    """Build ``VerifiedPaper`` entries from CONFIRMED verdicts only.

    Identity invariants (never-from-memory): the paper identity is the REGISTRY
    ``paper.label`` (the PDF we actually fetched), and the ``url`` is the registry
    url passed through ``safe_http_url`` — neither comes from the model. A paper whose
    url fails the allowlist yields no entries.
    """
    url = safe_http_url(paper.url, allowlist)
    if not url:
        return []
    by_key = {(c.paper_label, c.question): c for c in candidates}
    out: "list[VerifiedPaper]" = []
    for v in verdicts:
        if not getattr(v, "confirmed", False):
            continue
        cand = by_key.get((v.paper_label, v.question))
        q = (v.corrected_question or v.question or "").strip()
        marks = v.corrected_marks if v.corrected_marks is not None else (cand.marks if cand else None)
        summary = sanitize_summary(v.verified_summary or (cand.summary if cand else ""))
        if not summary:
            continue
        mark_bit = f" · {marks} marks" if marks is not None else ""
        q_bit = f" · {q}" if q else ""
        out.append(VerifiedPaper(label=f"{paper.label}{q_bit}{mark_bit}", summary=summary, url=url))
    return out


def assemble(intro, resources, verified, disclaimer) -> PastPapers:
    return PastPapers(intro=intro, resources=list(resources), verified=list(verified),
                      disclaimer=disclaimer)


def degrade(resolved: ResolvedSources) -> PastPapers:
    """Resources-only panel (signposting, no verified citations)."""
    return assemble(resolved.intro, resolved.where_to_get, [], resolved.disclaimer)


# ---------------------------------------------------------------------------
# Gemini / network
# ---------------------------------------------------------------------------

def fetch_pdf(url: str, allowlist: "tuple[str, ...]") -> "bytes | None":
    """Download a paper PDF (allowlisted host, size-capped, content-type checked).
    Returns None on any failure (fetch error, wrong type, not actually a PDF)."""
    safe = safe_http_url(url, allowlist)
    if not safe:
        return None
    try:
        data = helpers._http_get(safe, timeout=60, max_bytes=CONFIG.get("max_pdf_bytes", 15_000_000),
                                 accept_types=_PDF_ACCEPT)
    except Exception as exc:  # noqa: BLE001
        print(f"    paper fetch failed for {safe[:64]}: {exc}")
        return None
    if data[:5] != b"%PDF-":
        print(f"    not a PDF (missing %PDF- header): {safe[:64]}")
        return None
    return data


def generate_candidates(client, spec, pdf_part, paper_label: str) -> CandidateCitations:
    prompt = helpers.load_prompt("past_papers_candidates.txt").format(
        spec_block=helpers._spec_block(spec), paper_label=paper_label)
    return helpers.call_model(
        client, label=f"pp-cand:{spec.topic_id}", contents=[prompt, pdf_part],
        **helpers._gen_config("model_write", "temperature_verify", CandidateCitations))


def verify_candidates(client, spec, pdf_part, candidates) -> VerificationReport:
    listing = "\n".join(
        f"- paper_label={c.paper_label!r} question={c.question!r} marks={c.marks} "
        f"claim={c.summary!r} evidence={c.evidence_quote!r}"
        for c in candidates) or "(none)"
    prompt = helpers.load_prompt("past_papers_verify.txt").format(
        spec_block=helpers._spec_block(spec), candidates=listing)
    return helpers.call_model(
        client, label=f"pp-verify:{spec.topic_id}", contents=[prompt, pdf_part],
        **helpers._gen_config("model_paper_verify", "temperature_verify", VerificationReport))


def _process_one_paper(client, spec, paper, allowlist) -> "list[VerifiedPaper]":
    """Fetch + two-pass verify ONE paper. Returns its confirmed VerifiedPaper entries
    (may be empty). NEVER raises — one bad paper must not kill the topic — so it is the
    safe unit the stage fans out across papers."""
    pdf = fetch_pdf(paper.url, allowlist)
    if pdf is None:
        return []
    try:
        part = types.Part.from_bytes(data=pdf, mime_type="application/pdf")
        cands = generate_candidates(client, spec, part, paper.label).items
        if not cands:
            print(f"    paper '{paper.label}': no topic questions found")
            return []
        verdicts = verify_candidates(client, spec, part, cands).items
        got = confirmed_to_verified(cands, verdicts, paper, allowlist)
        print(f"    paper '{paper.label}': {len(cands)} candidate(s) -> {len(got)} verified")
        return got
    except Exception as exc:  # noqa: BLE001 — one bad paper must not kill the topic
        print(f"    paper '{paper.label}' skipped: {exc}")
        return []


def build_past_papers(client, spec) -> "PastPapers | None":
    """Full stage — NEVER raises. Returns a resources-only panel when no lawful paper
    is fetchable, or None when the board is not in the registry (panel omitted). Papers
    are fetched+verified CONCURRENTLY (each independent); results are collected in paper
    order so the panel is identical to the sequential build."""
    resolved = resolve_sources(spec)
    if resolved is None:
        return None
    papers = list(resolved.papers[:CONFIG.get("max_papers_per_topic", 3)])
    verified: "list[VerifiedPaper]" = []
    if papers:
        collected: list = [None] * len(papers)
        with ThreadPoolExecutor(max_workers=len(papers)) as ex:
            futs = {ex.submit(_process_one_paper, client, spec, p, resolved.fetch_allowlist): i
                    for i, p in enumerate(papers)}
            for fut in as_completed(futs):
                collected[futs[fut]] = fut.result()
        for got in collected:
            verified.extend(got or [])
    return assemble(resolved.intro, resolved.where_to_get, verified, resolved.disclaimer)
