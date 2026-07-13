#!/usr/bin/env python3
"""Ground the hand-seeded curriculum against official spec/CED PDFs (standalone CLI).

Every curriculum/*.json is "Hand-seeded for POC — validate against [spec]". This
verifies each LearningObjective (code + statement) and SpecChecklistItem (code +
can_do) against the official spec/CED PDF from sources.py, and AUTO-CORRECTS
high-confidence mismatches IN PLACE (the curriculum dir is git-tracked, so `git diff`
is the review backstop). Everything lower-confidence, plus anything the model cannot
find in the PDF ('absent'), is reported and never silently changed.

    py -3 ground_specs.py --list             # which topics have an official spec source
    py -3 ground_specs.py <topic_id>         # DRY RUN — report only (the default)
    py -3 ground_specs.py <topic_id> --apply # write high-confidence corrections in place
    py -3 ground_specs.py --all --apply      # whole corpus
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

import helpers
from config import CONFIG
from schemas import SpecGroundingReport
from sources import resolve_sources

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
_PDF_ACCEPT = ("application/pdf", "application/octet-stream")


# ---------------------------------------------------------------------------
# pure helpers (offline-testable)
# ---------------------------------------------------------------------------

def derive_keywords(spec) -> "list[str]":
    """Topic-relevant keywords for slicing a big CED down to its topic pages."""
    text = " ".join([spec.topic, spec.unit] + [lo.statement for lo in spec.learning_objectives])
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", text):
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            out.append(w)
    return out[:40]


def plan_changes(report: SpecGroundingReport, min_conf: str = "high"):
    """Split verdicts into (auto_apply, needs_attention). Only a 'corrected' verdict at
    >= min_conf auto-applies; 'absent' and lower-confidence corrections need a human."""
    thr = _CONF_ORDER.get(min_conf, 2)
    auto, attention = [], []
    for v in report.items:
        if v.status == "confirmed":
            continue
        if v.status == "corrected" and _CONF_ORDER.get(v.confidence, 0) >= thr:
            auto.append(v)
        else:
            attention.append(v)
    return auto, attention


def apply_to_spec_dict(spec_dict: dict, auto) -> list:
    """Apply corrections to a raw curriculum dict IN PLACE, matched by given_code;
    patches the code and/or the text field only. Returns the (kind, before, after)
    changes actually made. Callers pass ONLY the auto-apply list (see plan_changes)."""
    changed = []
    for v in auto:
        key = "learning_objectives" if v.kind == "objective" else "spec_checklist"
        text_key = "statement" if v.kind == "objective" else "can_do"
        for item in spec_dict.get(key, []):
            if item.get("code") == v.given_code:
                before = (item.get("code"), item.get(text_key))
                if v.corrected_code:
                    item["code"] = v.corrected_code
                if v.corrected_text:
                    item[text_key] = v.corrected_text
                after = (item.get("code"), item.get(text_key))
                if after != before:
                    changed.append((v.kind, before, after))
                break
    return changed


# ---------------------------------------------------------------------------
# network / model
# ---------------------------------------------------------------------------

def fetch_spec_pdf(url: str, allowlist) -> "bytes | None":
    from past_papers import safe_http_url
    safe = safe_http_url(url, allowlist)
    if not safe:
        print(f"    spec url not on the allowlist: {url[:70]}")
        return None
    try:
        data = helpers._http_get(safe, timeout=90, max_bytes=CONFIG.get("max_pdf_bytes", 15_000_000),
                                 accept_types=_PDF_ACCEPT)
    except Exception as exc:  # noqa: BLE001
        print(f"    spec fetch failed: {exc}")
        return None
    if data[:5] != b"%PDF-":
        print("    spec source did not return a PDF (likely an HTML landing page)")
        return None
    return data


def slice_pdf(pdf_bytes: bytes, keywords: "list[str]", threshold: int) -> bytes:
    """Slice a long CED to the pages mentioning the topic keywords (+ neighbours) so we
    don't send ~250 pages per topic. Falls back to the whole PDF on any issue."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return pdf_bytes
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count <= threshold:
            return pdf_bytes
        kws = [k.lower() for k in keywords if len(k) > 3]
        keep = set()
        for i in range(doc.page_count):
            low = doc.load_page(i).get_text("text").lower()
            if any(k in low for k in kws):
                keep.update({i - 1, i, i + 1})
        keep = sorted(p for p in keep if 0 <= p < doc.page_count)
        if not keep or len(keep) >= doc.page_count:
            return pdf_bytes
        out = fitz.open()
        for p in keep:
            out.insert_pdf(doc, from_page=p, to_page=p)
        data = out.tobytes()
        print(f"    sliced CED {doc.page_count} -> {len(keep)} page(s)")
        return data or pdf_bytes
    except Exception as exc:  # noqa: BLE001
        print(f"    pdf slice skipped: {exc}")
        return pdf_bytes


def verify_spec(client, spec, pdf_bytes) -> SpecGroundingReport:
    from google.genai import types
    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    los = "\n".join(f"- [objective] code={lo.code!r} text={lo.statement!r}" for lo in spec.learning_objectives)
    chk = "\n".join(f"- [checklist] code={it.code!r} text={it.can_do!r}" for it in spec.spec_checklist)
    items = "\n".join(x for x in (los, chk) if x) or "(none)"
    prompt = helpers.load_prompt("spec_ground.txt").format(
        board=spec.board, subject=spec.subject, level=spec.level, unit=spec.unit,
        topic=spec.topic, items=items)
    return helpers.call_model(client, label=f"spec-ground:{spec.topic_id}", contents=[prompt, part],
                              **helpers._gen_config("model_spec_ground", "temperature_verify", SpecGroundingReport))


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def _write_report(spec, report) -> None:
    d = Path(CONFIG["out_dir"]) / "spec_report"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec.topic_id}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")


def ground_topic(client, spec, *, apply: bool) -> bool:
    print(f"\n=== {spec.topic} ({spec.board} · {spec.subject}) ===")
    resolved = resolve_sources(spec)
    src = resolved.spec_source if resolved else None
    if src is None:
        print("  - no official spec source configured for this board+subject; skipped.")
        return False
    print(f"  spec source: {src.citation}")
    pdf = fetch_spec_pdf(src.url, resolved.fetch_allowlist)
    if pdf is None:
        return False
    pdf = slice_pdf(pdf, derive_keywords(spec), CONFIG.get("ced_slice_page_threshold", 40))
    report = verify_spec(client, spec, pdf)
    auto, attention = plan_changes(report, CONFIG.get("spec_autocorrect_min_confidence", "high"))
    n_conf = sum(1 for v in report.items if v.status == "confirmed")
    print(f"  verdicts: {n_conf} confirmed · {len(auto)} high-confidence correction(s) · "
          f"{len(attention)} need review")
    for v in attention:
        tag = "ABSENT" if v.status == "absent" else f"corrected/{v.confidence}"
        extra = (f" -> {v.corrected_code}" if v.corrected_code else "")
        extra += (f" : {v.corrected_text[:70]}" if v.corrected_text else "")
        print(f"    [{tag}] {v.kind} {v.given_code}{extra}")
    if report.missing_from_spec_note:
        print(f"    [MISSING FROM OUR JSON] {report.missing_from_spec_note[:200]}")
    _write_report(spec, report)

    if not auto:
        return False
    if not apply:
        print(f"  DRY RUN — {len(auto)} high-confidence correction(s) NOT written (use --apply):")
        for v in auto:
            print(f"    {v.kind} {v.given_code} -> {v.corrected_code or v.given_code}"
                  + (f" : {v.corrected_text[:70]}" if v.corrected_text else ""))
        return False
    path = Path(CONFIG["curriculum_dir"]) / f"{spec.topic_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = apply_to_spec_dict(data, auto)
    if changed:
        # indent=2 + ensure_ascii=False preserves key order + unicode -> a tight git diff.
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  ✓ applied {len(changed)} correction(s) to {path} (review with: git diff)")
    return bool(changed)


def _list_topics(topics) -> None:
    print("Topics and their official spec source (Phase C):")
    for tid, s in topics.items():
        r = resolve_sources(s)
        src = (r.spec_source.citation if r and r.spec_source else "— none configured")
        print(f"  {tid:46s} {src}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_dotenv()
    ap = argparse.ArgumentParser(description="Ground curriculum codes against official spec/CED PDFs.")
    ap.add_argument("topic_id", nargs="?", help="topic id (see --list)")
    ap.add_argument("--list", action="store_true", help="list topics + their spec source and exit")
    ap.add_argument("--all", action="store_true", help="ground every topic")
    ap.add_argument("--apply", action="store_true", help="WRITE high-confidence corrections (default: dry run)")
    args = ap.parse_args()

    topics = helpers.discover_topics()
    if args.list or (not args.topic_id and not args.all):
        _list_topics(topics)
        return

    if not args.all and args.topic_id not in topics:
        _list_topics(topics)
        sys.exit(f"\nUnknown topic '{args.topic_id}'.")

    client = helpers.get_gemini_client()
    specs = list(topics.values()) if args.all else [topics[args.topic_id]]
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] grounding {len(specs)} topic(s) — auto-apply confidence >= "
          f"{CONFIG.get('spec_autocorrect_min_confidence', 'high')}")
    changed = [s.topic_id for s in specs if ground_topic(client, s, apply=args.apply)]
    if args.apply and changed:
        print(f"\n✓ corrections written for {len(changed)} topic(s): {', '.join(changed)}")
        print("  Review with `git diff curriculum/` before committing.")
    elif not args.apply:
        print("\nDry run complete — re-run with --apply to write high-confidence corrections.")


if __name__ == "__main__":
    main()
