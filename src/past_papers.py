"""PDF-grounded past-paper stage (pure helpers + Gemini/network).

Three checks, grounded-not-recalled. For each fetchable paper PDF in the source registry
we (1) generate candidate citations by reading the PDF as a ``Part``, (2) independently
verify each against the SAME PDF, and (3) DETERMINISTICALLY require each survivor's
evidence quote to be present in that PDF's extracted text (``pdf_text.quote_supported``).

Step 3 is the one that makes "verified" mean something. Steps 1 and 2 are both model
calls, and model-checking-model fails in the same direction — that is how a fabricated
`Paper 1 · Q2(c)(i)` with an invented ionisation-energy table once shipped as verified.

A ``VerifiedPaper`` is ONLY ever built from a PDF fetched this run; its ``url`` and paper
identity come from the registry, never the model; and a citation whose evidence is not in
the document is dropped individually. ``resources[]`` signposting is filled for every
board; ``verified[]`` only where a lawful paper is fetchable AND provable, else the panel
degrades to resources-only (``build_past_papers``'s unconditional tail with an empty
list). ``build_past_papers`` never raises.

The pure helpers (no genai / no network) hold the security-critical assembly so the
offline smoke can exercise them without a key (see ``_smoke_past_papers.py``).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from google.genai import types

import helpers
import pdf_text
from config import CONFIG
from schemas import CandidateCitations, PastPapers, VerificationReport, VerifiedPaper
from sources import PaperSource, resolve_sources

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


# A model that cannot name the question has not cited one. These are the literal strings a
# JSON-shaped model emits for "I don't know" — and every one of them is TRUTHY, so the old
# `if q else ""` guard let them straight through and rendered
# `June 2023 · Paper 1 · null · 2 marks` to students.
_NULLISH_REF = frozenset({"null", "none", "nil", "n/a", "na", "n.a", "unknown", "undefined",
                          "tbc", "tbd", "-", "--", "?", ""})


def clean_question_ref(q: str) -> str:
    """A collapsed question reference, or '' when the model emitted a placeholder."""
    s = " ".join((q or "").split())
    return "" if s.casefold().strip(" .") in _NULLISH_REF else s


def confirmed_to_verified(candidates, verdicts, paper: PaperSource,
                          allowlist: "tuple[str, ...]", *,
                          paper_text) -> "list[VerifiedPaper]":
    """Build ``VerifiedPaper`` entries from verdicts whose EVIDENCE IS IN THE PDF.

    Identity invariants (never-from-memory): the paper identity is the REGISTRY
    ``paper.label`` (the PDF we actually fetched), and the ``url`` is the registry
    url passed through ``safe_http_url`` — neither comes from the model. A paper whose
    url fails the allowlist yields no entries.

    Evidence invariant: ``confirmed`` is a boolean a SECOND MODEL produced, so on its own
    it means only "a model agreed with a model" — and model-checking-model fails in the
    same direction, which is how an invented question with an invented data table once
    shipped as verified. Every entry must ALSO carry an ``evidence_quote`` provably copied
    out of the PDF fetched this run (``pdf_text.quote_supported``). A citation that fails
    is dropped INDIVIDUALLY: its siblings still ship, and the panel falls back to
    resources-only only when nothing survives anywhere.

    ``paper_text is None`` (a scan, unparseable bytes, or no PyMuPDF) yields NO entries —
    we cannot prove any of them, so we claim none.

    ``paper_text`` is keyword-only with NO DEFAULT, deliberately mirroring
    ``helpers.call_model``'s ``trace``: a future call site that forgets the evidence gate
    fails loudly at the call rather than silently reopening this hole.
    """
    url = safe_http_url(paper.url, allowlist)
    if not url or paper_text is None:
        return []
    by_key = {(c.paper_label, c.question): c for c in candidates}
    by_q = {c.question: c for c in candidates}   # models mangle paper_label, not the Q ref
    out: "list[VerifiedPaper]" = []
    for v in verdicts:
        if not getattr(v, "confirmed", False):
            continue
        # Fall back to matching on the question alone: a verifier that echoes paper_label
        # imprecisely would otherwise leave cand=None, hence no evidence quote, hence a
        # wrongly dropped REAL citation. The evidence check still runs on what it finds.
        cand = by_key.get((v.paper_label, v.question)) or by_q.get(v.question)
        q = (clean_question_ref(v.corrected_question)
             or clean_question_ref(v.question)
             or clean_question_ref(getattr(cand, "question", "")))
        if not q:
            print(f"    dropped (no usable question reference): {paper.label}")
            continue
        ok, why = pdf_text.quote_supported(getattr(cand, "evidence_quote", ""), paper_text)
        if not ok:
            print(f"    dropped (evidence not in the fetched PDF - {why}): {paper.label} · {q}")
            continue
        summary = sanitize_summary(v.verified_summary or (cand.summary if cand else ""))
        if not summary:
            continue
        marks = v.corrected_marks if v.corrected_marks is not None else (cand.marks if cand else None)
        # `marks_ok=False` with no correction offered means the verifier declined to endorse
        # the total. WITHHOLD it rather than print an unendorsed number: reading a model
        # boolean to NARROW a claim is safe; only reading one to ADMIT a claim is not.
        if marks is not None and not getattr(v, "marks_ok", False) and v.corrected_marks is None:
            marks = None
        mark_bit = f" · {marks} marks" if marks is not None else ""
        out.append(VerifiedPaper(label=f"{paper.label} · {q}{mark_bit}", summary=summary, url=url))
    return out


def assemble(intro, resources, verified, disclaimer) -> PastPapers:
    return PastPapers(intro=intro, resources=list(resources), verified=list(verified),
                      disclaimer=disclaimer)


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
        client, trace=helpers.trace_topic(spec, "past_papers.candidates"),
        contents=[prompt, pdf_part],
        **helpers._gen_config("model_write", "temperature_verify", CandidateCitations))


def verify_candidates(client, spec, pdf_part, candidates) -> VerificationReport:
    listing = "\n".join(
        f"- paper_label={c.paper_label!r} question={c.question!r} marks={c.marks} "
        f"claim={c.summary!r} evidence={c.evidence_quote!r}"
        for c in candidates) or "(none)"
    prompt = helpers.load_prompt("past_papers_verify.txt").format(
        spec_block=helpers._spec_block(spec), candidates=listing)
    return helpers.call_model(
        client, trace=helpers.trace_topic(spec, "past_papers.verify"),
        contents=[prompt, pdf_part],
        **helpers._gen_config("model_paper_verify", "temperature_verify", VerificationReport))


def _process_one_paper(client, spec, paper, allowlist) -> "list[VerifiedPaper]":
    """Fetch + two-pass verify ONE paper. Returns its confirmed VerifiedPaper entries
    (may be empty). NEVER raises — one bad paper must not kill the topic — so it is the
    safe unit the stage fans out across papers."""
    pdf = fetch_pdf(paper.url, allowlist)
    if pdf is None:
        return []
    try:
        # Extract BEFORE the model calls: a scanned paper can never yield a PROVABLE
        # citation, so bailing here is both the honest answer and two Gemini calls saved.
        text = pdf_text.extract_text(pdf)
        if text is None:
            print(f"    paper '{paper.label}': no verifiable text layer -> 0 citations")
            return []
        part = types.Part.from_bytes(data=pdf, mime_type="application/pdf")
        cands = generate_candidates(client, spec, part, paper.label).items
        if not cands:
            print(f"    paper '{paper.label}': no topic questions found")
            return []
        verdicts = verify_candidates(client, spec, part, cands).items
        got = confirmed_to_verified(cands, verdicts, paper, allowlist, paper_text=text)
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
