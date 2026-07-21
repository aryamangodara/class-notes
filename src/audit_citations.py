#!/usr/bin/env python3
"""Audit already-shipped past-paper citations against the real PDFs (standalone CLI).

Pages generated before the deterministic evidence gate landed carry citations that were
"verified" by a SECOND MODEL only — and model-checking-model fails in the same direction.
This re-checks each shipped citation against the paper PDF it names and reports which ones
cannot be located. It makes ZERO Gemini calls and NEVER rewrites a generated note.

    py -3 src/audit_citations.py --list        # citations per topic + papers to fetch (NO network)
    py -3 src/audit_citations.py               # audit every out/**/*.v2.json (default)
    py -3 src/audit_citations.py <topic_id>    # audit one topic
    py -3 src/audit_citations.py --strict      # exit 1 if any citation FAILS (CI gate)
    py -3 src/audit_citations.py --no-report   # stdout only, write nothing at all

WHY A DIFFERENT NEEDLE FROM THE RUNTIME GATE. `evidence_quote` is not persisted into
`.v2.json` (deliberately — shipping verbatim exam text into every rendered page is a
licensing problem), so the audit cannot re-run the runtime check. It probes the OTHER half
instead: the question reference embedded in the label, anchored line-initially in the PDF.
That is the right trade here because the economics invert — a runtime false negative
silently deletes a real citation from a live page, whereas an audit false negative costs a
human 30 seconds. Runtime optimises precision; the audit optimises recall of problems.
Together they cover invented CONTENT and invented QUESTION NUMBERS; neither alone does.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pdf_text
from config import CONFIG
from past_papers import fetch_pdf
from sources import resolve_sources

# What --strict exits non-zero on. DELIBERATELY NARROW: `unfetchable` and `no-ref` are
# hard facts about our own artifact (a dead link, a label with no question in it), whereas
# `unconfirmed` is only "this PDF's text did not yield the question" — layout, OCR and
# per-board conventions all produce false negatives, so it must never fail a build.
# Measured on the live corpus: a naive probe called 3 of 5 GENUINE citations absent.
FAIL_VERDICTS = ("no-ref", "unfetchable")


# What a question reference actually looks like: "Q5", "3(b)(ii)", "Q6 (d)", "12(a)".
# Position alone cannot identify it, because the REGISTRY paper labels themselves contain
# middots ("2024 · Free-response questions"), so a paper-only label would otherwise parse
# its own name ("FRQ") as a question and be audited as a missing question.
_REF_RE = re.compile(r"^Q?\s*\d{1,2}\s*(\(\s*[A-Za-z0-9]{1,4}\s*\)\s*)*$", re.I)


def parse_label(label: str) -> "tuple[str, str, int | None]":
    """Split a rendered citation label back into (paper, question_ref, marks).

    Labels are built by ``past_papers.confirmed_to_verified`` as
    ``"<registry paper> · <question> [· <n> marks]"``. Parsing is only ever used to
    RE-CHECK a claim, never to make one, so a label whose last segment is not
    recognisably a question reference degrades to ('', '', marks) and is reported as
    `no-ref` rather than raising or guessing.
    """
    parts = [p.strip() for p in (label or "").split("·") if p.strip()]
    if not parts:
        return "", "", None
    marks = None
    if len(parts) > 1 and parts[-1].lower().endswith(("mark", "marks", "point", "points")):
        head = parts[-1].split()[0]
        if head.isdigit():
            marks = int(head)
        parts = parts[:-1]
    if len(parts) < 2 or not _REF_RE.match(parts[-1]):
        return " · ".join(parts), "", marks
    return " · ".join(parts[:-1]), parts[-1], marks


def audit_citation(label: str, paper_text) -> "tuple[str, str]":
    """-> (verdict, detail). Pure given the extracted text; the smoke test pins it."""
    _, ref, _ = parse_label(label)
    if not ref:
        return "no-ref", "label carries no question reference"
    page = pdf_text.locate_question(ref, paper_text)
    if page is None:
        return "unconfirmed", (f"could not resolve {ref!r} in this PDF's text - check by hand "
                               "before assuming it is wrong")
    # A bare question number with no sub-part constrains very little on its own.
    weak = " (weak: a bare question number matches broadly)" \
        if len(pdf_text.question_ref_tokens(ref)) < 2 else ""
    return "located", f"page {page}{weak}"


def _iter_notes(out: Path, topic_id: str = "") -> "list[Path]":
    found = []
    for p in sorted(out.rglob("*.v2.json")):
        if "citation_audit" in p.relative_to(out).parts or "spotcheck" in p.relative_to(out).parts:
            continue
        if topic_id and p.name != f"{topic_id}.v2.json":
            continue
        found.append(p)
    return found


def _citations(data: dict) -> "list[dict]":
    return ((data.get("past_papers") or {}).get("verified") or [])


def audit_note(path: Path, cache: dict) -> "list[dict]":
    """Audit one generated note. ``cache`` memoises url -> bytes for the process: a
    91-topic AP Chemistry corpus cites the SAME two FRQ PDFs, so without it the audit
    would refetch each of them ~90 times. ``fetch_pdf`` stays deliberately stateless."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cites = _citations(data)
    if not cites:
        return []
    # Re-resolve the allowlist from the registry rather than trusting the file: the urls
    # come from JSON on disk, so re-gating them means a hand-edited or tampered .v2.json
    # cannot turn this audit into an SSRF primitive.
    resolved = resolve_sources(SimpleNamespace(board=data.get("board", ""),
                                               subject=data.get("subject", "")))
    allowlist = resolved.fetch_allowlist if resolved else ()
    rows = []
    for c in cites:
        url, label = c.get("url", ""), c.get("label", "")
        if url not in cache:
            pdf = fetch_pdf(url, allowlist) if allowlist else None
            cache[url] = pdf_text.extract_text(pdf) if pdf else None
        text = cache[url]
        if text is None:
            verdict, detail = "unfetchable", "PDF could not be fetched, parsed, or has no text layer"
        else:
            verdict, detail = audit_citation(label, text)
        rows.append({"topic_id": path.name[: -len(".v2.json")], "label": label,
                     "url": url, "verdict": verdict, "detail": detail})
    return rows


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Re-check shipped past-paper citations against the real PDFs.")
    ap.add_argument("topic_id", nargs="?", default="", help="audit only this topic")
    ap.add_argument("--list", action="store_true", help="list citations + papers, fetch nothing")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any citation fails")
    ap.add_argument("--no-report", action="store_true", help="stdout only; write no files")
    args = ap.parse_args()

    out = Path(CONFIG["out_dir"])
    notes = _iter_notes(out, args.topic_id)
    if not notes:
        print(f"No generated notes in {out}/ matching that selection.")
        return

    if args.list:
        total, urls = 0, set()
        for p in notes:
            cites = _citations(json.loads(p.read_text(encoding="utf-8")))
            if cites:
                print(f"  {p.name[: -len('.v2.json')]:46s} {len(cites)} citation(s)")
                total += len(cites)
                urls.update(c.get("url", "") for c in cites)
        print(f"\n{total} citation(s) across {len(notes)} note(s); {len(urls)} distinct paper PDF(s) to fetch.")
        return

    cache: dict = {}
    rows: "list[dict]" = []
    for p in notes:
        rows.extend(audit_note(p, cache))
    if not rows:
        print(f"No past-paper citations in {len(notes)} note(s) — nothing to audit.")
        return

    by_topic: "dict[str, list[dict]]" = {}
    for r in rows:
        by_topic.setdefault(r["topic_id"], []).append(r)
    for tid, rs in sorted(by_topic.items()):
        print(f"\n{tid}")
        for r in rs:
            mark = "OK " if r["verdict"] == "located" else "!! "
            print(f"  {mark}[{r['verdict']}] {r['label']}")
            print(f"       {r['detail']}")

    failed = [r for r in rows if r["verdict"] in FAIL_VERDICTS]
    unconfirmed = [r for r in rows if r["verdict"] == "unconfirmed"]
    located = len(rows) - len(failed) - len(unconfirmed)
    print(f"\n{len(rows)} citation(s) audited across {len(by_topic)} topic(s): "
          f"{located} located, {len(unconfirmed)} unconfirmed, {len(failed)} actionable.")
    if unconfirmed:
        print("UNCONFIRMED is not a verdict of fabrication - the location probe is "
              "format-sensitive. Check by hand, or regenerate to re-run the evidence gate.")
    if failed or unconfirmed:
        for tid in sorted({r["topic_id"] for r in failed + unconfirmed}):
            print(f"  py -3 src/notes.py {tid} --force")

    if not args.no_report:
        dest = out / "citation_audit"
        dest.mkdir(parents=True, exist_ok=True)
        for tid, rs in by_topic.items():
            (dest / f"{tid}.json").write_text(json.dumps(rs, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
        lines = ["# Citation audit", "",
                 f"{len(rows)} citation(s), {len(failed)} actionable.", ""]
        lines += [f"- **{r['verdict']}** — `{r['topic_id']}` — {r['label']} ({r['detail']})"
                  for r in rows]
        (dest / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Report written to {dest}/")

    if args.strict and failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
