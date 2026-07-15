#!/usr/bin/env python3
"""Class Notes generator — entry point / batch runner.

Feed a topic; get grounded, board-aligned INTERACTIVE class notes: the block
structure as ``out/<board>/<subject>/<id>.v2.json`` (the source of truth) plus a
self-contained sibling ``<id>.interactive.html`` that renders it client-side.

The curriculum is FETCHED separately (``extract_specs.py`` -> ``ground_specs.py`` ->
review -> ``approve_specs.py``); this command GENERATES notes from it, repeatably:

    python notes.py --list                       # show discovered topics
    python notes.py ap-bio-cellular-respiration  # generate one topic
    python notes.py --all                        # generate every topic (skips existing)
    python notes.py --subject Chemistry          # generate a board/subject/level slice
    python notes.py --all --force --jobs 3       # regenerate everything, 3 topics in parallel
    python notes.py --all --dry-run              # print the plan; no Gemini calls, no writes

Batch defaults (fast + safe): skips topics whose output already exists (``--force`` to
regenerate), skips specs still marked UNVERIFIED (``--include-unverified`` to include),
isolates per-topic failures (one bad topic never aborts the run), generates topics in
parallel (``--jobs``, no quality change), and writes ``out/run-manifest.json``.

Grounding lives in curriculum/*.json (one TopicSpec per topic), discovered automatically.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import batch
from config import CONFIG
from helpers import discover_topics, get_gemini_client


def _print_topics(topics) -> None:
    if not topics:
        print("No curriculum specs found in curriculum/. Add a TopicSpec JSON.")
        return
    print("Discovered topics:")
    for tid, s in topics.items():
        mark = "  (UNVERIFIED)" if batch.is_unverified(s) else ""
        print(f"  {tid:46s} {s.board} | {s.subject} | {s.level} - {s.topic}{mark}")


def run_one(client, spec) -> "batch.TopicResult":
    """Generate + save one topic, returning a structured ``TopicResult``. NEVER
    propagates: a coverage/structural/any failure is captured as an outcome so one bad
    topic can't abort a batch run. ``CoverageError``/``StructuralError`` (both subclass
    ``RuntimeError``) are classified before the generic ``Exception`` catch."""
    from pipeline_v2 import generate_interactive_notes, save_interactive_notes, _fs_safe
    from coverage_gate import CoverageError, StructuralError
    t0 = time.monotonic()
    base = dict(topic_id=spec.topic_id, board=spec.board, subject=spec.subject)
    print(f"\n=== {spec.topic} ({spec.board} · {spec.subject}) ===")
    try:
        notes = generate_interactive_notes(client, spec)
        save_interactive_notes(notes)
        out = str(Path(CONFIG["out_dir"]) / _fs_safe(spec.board) / _fs_safe(spec.subject)
                  / f"{spec.topic_id}.v2.json")
        return batch.TopicResult(**base, outcome=batch.OUTCOME_WRITTEN, output_path=out,
                                 elapsed_s=time.monotonic() - t0)
    except CoverageError as exc:
        print(f"  x COVERAGE FAILED — nothing written for {spec.topic_id}.\n    {exc}")
        return batch.TopicResult(**base, outcome=batch.OUTCOME_COVERAGE_FAILED,
                                 detail=str(exc), elapsed_s=time.monotonic() - t0)
    except StructuralError as exc:
        print(f"  x STRUCTURAL FAILED — nothing written for {spec.topic_id}.\n    {exc}")
        return batch.TopicResult(**base, outcome=batch.OUTCOME_STRUCTURAL_FAILED,
                                 detail=str(exc), elapsed_s=time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001 — one topic must NEVER abort the batch
        print(f"  x ERROR — {type(exc).__name__} for {spec.topic_id}: {exc}")
        return batch.TopicResult(**base, outcome=batch.OUTCOME_ERROR,
                                 detail=f"{type(exc).__name__}: {exc}", elapsed_s=time.monotonic() - t0)


def _existing_output_ids(out_dir: str) -> "set[str]":
    """topic_ids that already have generated output. rglob because out/ is nested
    <board>/<subject>/; topic_ids are globally unique, so an id-set is a sound skip key.
    The spotcheck bundle dir is excluded (it holds no .v2.json, but be defensive)."""
    root = Path(out_dir)
    if not root.exists():
        return set()
    return {p.name[: -len(".v2.json")] for p in root.rglob("*.v2.json")
            if "spotcheck" not in p.relative_to(root).parts}


def run_batch(client, specs, *, jobs: int) -> "list[batch.TopicResult]":
    """Generate a list of topics: sequential (jobs<=1, unchanged behavior) or across an
    outer pool of ``jobs`` workers. In parallel mode stdout is wrapped so each worker's
    top-level lines are tagged with its topic_id. run_one never raises, so no future
    here errors."""
    if jobs <= 1:
        return [run_one(client, s) for s in specs]
    results: "list[batch.TopicResult]" = []
    real_stdout = sys.stdout
    tagged = batch.TaggedStdout(real_stdout)
    sys.stdout = tagged

    def _work(s):
        tagged.set_tag(s.topic_id)
        return run_one(client, s)

    try:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for fut in as_completed([ex.submit(_work, s) for s in specs]):
                results.append(fut.result())
    finally:
        sys.stdout = real_stdout
    return results


def _write_manifest(results, *, jobs, selected, elapsed) -> None:
    out = Path(CONFIG["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "jobs": jobs, "selected": selected, "elapsed_s": round(elapsed, 1)}
    manifest = batch.build_manifest(results, meta=meta)
    (out / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  manifest -> {out / 'run-manifest.json'}")


def _run_single(topic_id: str, topics) -> int:
    if topic_id not in topics:
        _print_topics(topics)
        sys.exit(f"\nUnknown topic '{topic_id}'.")
    spec = topics[topic_id]
    if batch.is_unverified(spec):
        print(f"  ! {spec.topic_id} is UNVERIFIED (auto-extracted) — generating anyway "
              f"(explicit single-topic request).")
    client = get_gemini_client()
    res = run_one(client, spec)
    return 1 if res.outcome in batch.FAILURE_OUTCOMES else 0


def _run_batch_mode(args, topics) -> int:
    selected = batch.select_specs(topics.values(), board=args.board, subject=args.subject, level=args.level)
    if not selected:
        print("No topics match that selection. Available:")
        _print_topics(topics)
        return 2
    existing = _existing_output_ids(CONFIG["out_dir"])
    to_gen, skipped = batch.plan_batch(
        selected, existing, force=args.force,
        include_unverified=args.include_unverified, limit=args.limit)
    n_exist = sum(1 for r in skipped if r.outcome == batch.OUTCOME_SKIPPED_EXISTING)
    n_unv = sum(1 for r in skipped if r.outcome == batch.OUTCOME_SKIPPED_UNVERIFIED)
    print(f"selected {len(selected)} | generate {len(to_gen)} | skip-existing {n_exist} | "
          f"skip-unverified {n_unv}" + (f" | limit {args.limit}" if args.limit is not None else ""))

    if args.dry_run:
        for s in to_gen:
            print(f"  would generate: {s.topic_id}")
        if n_unv:
            print(f"  ({n_unv} UNVERIFIED spec(s) skipped — after review: "
                  f"py -3 src/approve_specs.py <selector> --apply)")
        return 0
    if not to_gen:
        print("Nothing to generate.")
        if n_unv:
            print(f"  {n_unv} spec(s) are UNVERIFIED — approve them (approve_specs) or pass --include-unverified.")
        if n_exist:
            print(f"  {n_exist} topic(s) already generated — pass --force to regenerate.")
        return 0

    jobs = args.jobs if args.jobs is not None else CONFIG.get("max_parallel_topics", 1)
    jobs = max(1, min(jobs, len(to_gen)))
    client = get_gemini_client()  # fail fast (clear message) if no key BEFORE we start
    print(f"generating {len(to_gen)} topic(s), --jobs {jobs} ...")
    results = list(skipped)
    t0 = time.monotonic()
    try:
        results += run_batch(client, to_gen, jobs=jobs)
    finally:
        elapsed = time.monotonic() - t0
        _write_manifest(results, jobs=jobs, selected=len(selected), elapsed=elapsed)
        print(batch.format_summary(results, elapsed_s=elapsed))
    return 1 if batch.any_failures(results) else 0


def main() -> None:
    # Windows consoles default to cp1252; allow unicode (·, —) in our output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_dotenv()
    ap = argparse.ArgumentParser(description="Generate grounded, interactive class notes.")
    ap.add_argument("topic_id", nargs="?", help="single topic id (see --list)")
    ap.add_argument("--list", action="store_true", help="list discovered topics and exit")
    ap.add_argument("--all", action="store_true", help="batch over every discovered topic")
    ap.add_argument("--board", help="batch filter: only this board")
    ap.add_argument("--subject", help="batch filter: only this subject")
    ap.add_argument("--level", help="batch filter: only this level")
    ap.add_argument("--force", action="store_true", help="regenerate topics whose output already exists")
    ap.add_argument("--include-unverified", action="store_true",
                    help="also generate specs still marked UNVERIFIED (default: skip them)")
    ap.add_argument("--jobs", type=int, default=None,
                    help=f"topics to generate in parallel (default: CONFIG max_parallel_topics="
                         f"{CONFIG.get('max_parallel_topics', 1)}); pure parallelism, no quality change")
    ap.add_argument("--limit", type=int, default=None, help="cap topics generated this run (cheap first run)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit (no Gemini calls, no writes)")
    args = ap.parse_args()

    topics = discover_topics()
    if args.list:
        _print_topics(topics)
        return
    if not topics:
        sys.exit("No curriculum specs found in curriculum/.")

    batch_mode = args.all or any([args.board, args.subject, args.level])
    if args.topic_id and batch_mode:
        sys.exit("Give a single topic_id OR batch flags (--all/--board/--subject/--level), not both.")

    if args.topic_id:
        sys.exit(_run_single(args.topic_id, topics))
    if not batch_mode:
        _print_topics(topics)
        return
    sys.exit(_run_batch_mode(args, topics))


if __name__ == "__main__":
    main()
