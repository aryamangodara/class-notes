#!/usr/bin/env python3
"""Extract curriculum TopicSpecs from official spec/CED PDFs (standalone CLI).

The curriculum store is the moat; this GROWS it from the source of record. For a
(board, subject) whose official spec PDF is registered in sources.py, it fetches the
PDF, ENUMERATES its teachable topics, then extracts ONE grounded TopicSpec per topic
into curriculum/<id>.json — so `notes.py --all` can then generate the whole corpus
from a single command.

Grounded-not-recalled + human-gated, like the rest of the repo:
  * every objective/code is extracted from the fetched PDF (never model memory);
  * extraction fills only the objective/depth/assessment half of a TopicSpec — the
    curated exam-format layer (exam_map / past_papers / next_topic) is left empty for
    a human;
  * each written spec is stamped UNVERIFIED, and the CLI points you at ground_specs.py
    (verify codes vs the SAME PDF) + `git diff` review before it ships. DRY RUN is the
    default; --apply writes; existing ids are skipped unless --force.

    py -3 src/extract_specs.py --list                                          # registered sources + coverage
    py -3 src/extract_specs.py --board "AP (College Board)" --subject Chemistry           # DRY RUN
    py -3 src/extract_specs.py --board "AP (College Board)" --subject Chemistry --apply --limit 5
    py -3 src/extract_specs.py --board "AP (College Board)" --apply            # every registered subject of a board
    py -3 src/extract_specs.py --all --apply                                   # every registered spec source
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from google.genai import types

import helpers
from config import CONFIG, board_to_level
from ground_specs import fetch_spec_pdf, slice_pdf
from schemas import SpecTopicList, TopicSpec
from sources import _SPEC_SOURCES, resolve_sources

# Slug pieces matched to the existing corpus convention (ap-bio-…, alevel-maths-…) so a
# re-extract of a known topic collides on id and is skipped, not duplicated.
_LEVEL_PREFIX = {"AP": "ap", "IGCSE": "igcse", "A-Level": "alevel", "SAT": "sat", "AMC 10": "amc10"}
_SUBJECT_ABBR = {"Biology": "bio", "Chemistry": "chem", "Physics": "physics",
                 "Mathematics": "maths", "Reading and Writing": "english"}


# ---------------------------------------------------------------------------
# pure helpers (no genai / no network; offline-testable)
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Filename-safe slug: lowercase alphanumeric runs joined by single hyphens."""
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower())) or "topic"


def derive_topic_id(board: str, subject: str, topic: str) -> str:
    """Deterministic curriculum id '<level>-<subject>-<topic>' (e.g.
    'ap-bio-cellular-respiration'), matching the corpus convention."""
    lp = _LEVEL_PREFIX.get(board_to_level(board), slugify(board_to_level(board)))
    sa = _SUBJECT_ABBR.get(subject) or slugify(subject)
    return f"{lp}-{sa}-{slugify(topic)}"


def plan_writes(board, subject, entries, existing_ids, *, force=False, limit=None):
    """Split enumerated topics into (to_write, skipped) by id-collision with the
    existing corpus. Pure: each entry needs only a `.topic`. Returns lists of
    (topic_id, entry). `limit` caps to_write (a cheap first live run)."""
    planned, skipped = [], []
    for e in entries:
        tid = derive_topic_id(board, subject, e.topic)
        (skipped if (tid in existing_ids and not force) else planned).append((tid, e))
    if limit is not None:
        planned = planned[:limit]
    return planned, skipped


def stamp_extracted(spec_dict: dict, *, topic_id, board, subject, level, unit, topic,
                    citation: str, page_note: str = "") -> dict:
    """Overwrite the controlled identity fields deterministically (never trust the model
    for board/level/id), clear the human-curated exam-format layer, and stamp UNVERIFIED
    provenance. Returns a new dict ready to validate + write."""
    d = dict(spec_dict)
    d["topic_id"], d["board"], d["subject"], d["level"] = topic_id, board, subject, level
    d["unit"], d["topic"] = unit, topic
    # The curated exam-format layer is a human decision — extraction never authors it.
    d["exam_map"], d["past_papers"], d["next_topic"] = [], None, ""
    if not d.get("spec_source_citation"):
        d["spec_source_citation"] = citation
    pg = f", {page_note}" if page_note else ""
    d["source"] = (f"Auto-extracted from {citation}{pg} — UNVERIFIED: run "
                   f"`py -3 src/ground_specs.py {topic_id} --apply` then review `git diff` before shipping.")
    return d


# ---------------------------------------------------------------------------
# network / model
# ---------------------------------------------------------------------------

def enumerate_topics(client, board, subject, level, pdf_bytes) -> list:
    """Stage 1: list the teachable topics in a whole subject spec PDF."""
    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    prompt = helpers.load_prompt("spec_enumerate.txt").format(board=board, subject=subject, level=level)
    result = helpers.call_model(
        client, trace=helpers.trace_spec_run("spec.enumerate", board=board, subject=subject,
                                             level=level),
        contents=[prompt, part],
        **helpers._gen_config("model_spec_enumerate", "temperature_verify", SpecTopicList))
    items = list(result.items)
    cap = CONFIG.get("max_topics_per_spec", 120)
    if len(items) > cap:  # never truncate silently (repo doctrine): say what was dropped.
        print(f"    NOTE: enumerated {len(items)} topics; capping to {cap}. Raise "
              f"CONFIG['max_topics_per_spec'] to extract the rest.")
        items = items[:cap]
    return items


def extract_topic(client, board, subject, level, entry, pdf_bytes) -> dict:
    """Stage 2: extract ONE TopicSpec from the pages this topic's keywords slice to."""
    sliced = slice_pdf(pdf_bytes, list(entry.keywords), CONFIG.get("ced_slice_page_threshold", 40))
    part = types.Part.from_bytes(data=sliced, mime_type="application/pdf")
    prompt = helpers.load_prompt("spec_extract.txt").format(
        board=board, subject=subject, level=level, unit=entry.unit, topic=entry.topic)
    spec = helpers.call_model(
        client, trace=helpers.trace_spec_run("spec.extract", board=board, subject=subject,
                                             level=level, item=entry.topic),
        contents=[prompt, part],
        **helpers._gen_config("model_spec_extract", "temperature_verify", TopicSpec))
    return spec.model_dump()


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def _existing_ids() -> "set[str]":
    # Filenames only (no full validation) so a stray file can't break id discovery.
    return {p.stem for p in Path(CONFIG["curriculum_dir"]).glob("*.json")}


def extract_source(client, board, subject, *, apply, force, limit) -> "list[str]":
    """Enumerate + extract one (board, subject) spec source. Returns the written ids."""
    print(f"\n=== {board} · {subject} ===")
    resolved = resolve_sources(SimpleNamespace(board=board, subject=subject))
    src = resolved.spec_source if resolved else None
    if src is None:
        print("  - no official spec source registered for this board+subject; skipped.")
        return []
    print(f"  spec source: {src.citation}")
    pdf = fetch_spec_pdf(src.url, resolved.fetch_allowlist)
    if pdf is None:
        return []
    level = board_to_level(board)
    entries = enumerate_topics(client, board, subject, level, pdf)
    if not entries:
        print("  - no topics enumerated from this PDF (not a spec, or unreadable).")
        return []
    planned, skipped = plan_writes(board, subject, entries, _existing_ids(), force=force, limit=limit)
    print(f"  enumerated {len(entries)} topic(s): {len(planned)} to extract, "
          f"{len(skipped)} already in curriculum{' (use --force to overwrite)' if skipped else ''}.")
    if not apply:
        print(f"  DRY RUN — would write {len(planned)} spec(s) (use --apply):")
        for tid, e in planned:
            print(f"    {tid:52s} <- {e.topic}")
        return []
    if not planned:
        return []

    citation = src.citation
    curric = Path(CONFIG["curriculum_dir"])

    def _work(item):
        tid, e = item
        try:
            raw = extract_topic(client, board, subject, level, e, pdf)
            stamped = stamp_extracted(raw, topic_id=tid, board=board, subject=subject, level=level,
                                      unit=e.unit, topic=e.topic, citation=citation)
            TopicSpec.model_validate(stamped)  # never write an invalid spec
            return tid, stamped, None
        except Exception as exc:  # noqa: BLE001 — one bad topic must not kill the source
            return tid, None, exc

    written: "list[str]" = []
    with ThreadPoolExecutor(max_workers=CONFIG["max_parallel_sections"]) as ex:
        futures = [ex.submit(_work, it) for it in planned]
        for f in as_completed(futures):
            tid, stamped, err = f.result()
            if err is not None or stamped is None:
                print(f"    ✗ {tid}: {err}")
                continue
            (curric / f"{tid}.json").write_text(
                json.dumps(stamped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            written.append(tid)
            print(f"    ✓ {tid}")
    return sorted(written)


def _registered() -> "list[tuple[str, str]]":
    return sorted(_SPEC_SOURCES.keys())


def _list_sources() -> None:
    existing = _existing_ids()
    print("Registered official spec sources (board · subject) and current curriculum coverage:")
    for board, subject in _registered():
        pref = f"{_LEVEL_PREFIX.get(board_to_level(board), '')}-{_SUBJECT_ABBR.get(subject, slugify(subject))}-"
        have = sum(1 for i in existing if i.startswith(pref))
        print(f"  {board:22s} {subject:22s} {have:2d} topic(s)  <- {_SPEC_SOURCES[(board, subject)].citation}")
    print("\nA (board, subject) with no entry has no spec PDF configured — add one to "
          "_SPEC_SOURCES in sources.py to switch it on.")


def _select(args) -> "list[tuple[str, str]]":
    if args.all:
        return _registered()
    if args.board and args.subject:
        return [(args.board, args.subject)]
    if args.board:
        return [(b, s) for (b, s) in _registered() if b == args.board]
    return []


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_dotenv()
    ap = argparse.ArgumentParser(description="Extract curriculum TopicSpecs from official spec/CED PDFs.")
    ap.add_argument("--board", help="board string, e.g. 'AP (College Board)'")
    ap.add_argument("--subject", help="subject, e.g. 'Chemistry'")
    ap.add_argument("--all", action="store_true", help="every registered spec source")
    ap.add_argument("--list", action="store_true", help="list registered spec sources + coverage and exit")
    ap.add_argument("--apply", action="store_true", help="WRITE curriculum JSON (default: dry run)")
    ap.add_argument("--force", action="store_true", help="overwrite existing curriculum ids")
    ap.add_argument("--limit", type=int, default=None, help="cap topics extracted per source (a cheap first run)")
    args = ap.parse_args()

    targets = _select(args)
    if args.list or not targets:
        _list_sources()
        if not args.list:
            print("\nNothing selected — use --all, or --board [--subject].")
        return

    client = helpers.get_gemini_client()
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] extracting {len(targets)} spec source(s)"
          + (f", limit {args.limit}/source" if args.limit else ""))
    written: "list[str]" = []
    for board, subject in targets:
        written += extract_source(client, board, subject, apply=args.apply, force=args.force, limit=args.limit)
    if args.apply:
        print(f"\n✓ wrote {len(written)} spec(s) to curriculum/.")
        if written:
            print("  NEXT — verify + review before generating (extraction is UNVERIFIED):")
            print("    py -3 src/ground_specs.py --all --apply   # verify codes vs the spec PDF")
            print("    git diff curriculum/                      # human review")
            print("    py -3 src/notes.py --all                  # generate notes")
    else:
        print("\nDry run complete — re-run with --apply to write curriculum JSON.")


if __name__ == "__main__":
    main()
