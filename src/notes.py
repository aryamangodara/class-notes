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
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import batch
from config import CONFIG
from helpers import discover_topics, flush_langfuse, get_gemini_client

# Live-progress files (written during a batch; read by --status). The durable truth
# is the .v2.json count on disk; these add the current run's in-flight/rate/ETA view.
_PROGRESS_LOCK = threading.Lock()


def _status_path() -> Path:
    return Path(CONFIG["out_dir"]) / "run-status.json"


def _progress_path() -> Path:
    return Path(CONFIG["out_dir"]) / "run-progress.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_events(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except Exception:
                pass  # tolerate a half-written trailing line mid-append
    return events


def _write_status(st: dict) -> None:
    try:
        _status_path().write_text(json.dumps(st, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass  # status/progress I/O must NEVER break a generation run


def _append_progress(event: dict) -> None:
    rec = {**event, "at": _now_iso()}
    try:
        with _PROGRESS_LOCK, open(_progress_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _begin_run(to_gen, *, jobs: int, selector: dict) -> None:
    """Stamp the run header + start a fresh progress window for this run."""
    Path(CONFIG["out_dir"]).mkdir(parents=True, exist_ok=True)
    try:
        _progress_path().write_text("", encoding="utf-8")
    except Exception:
        pass
    _write_status({"active": True, "pid": os.getpid(), "host": socket.gethostname(),
                   "started_at": _now_iso(), "ended_at": None,
                   "to_generate": len(to_gen), "jobs": jobs, "selector": selector})


def _end_run() -> None:
    st = _read_json(_status_path()) or {}
    st.update(active=False, ended_at=_now_iso())
    _write_status(st)


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
        # An UNVERIFIED spec only reaches here via --include-unverified, i.e. someone
        # overrode a DETERMINISTIC grounding failure. Record that on the page itself so it
        # surfaces in the spot-check queue (spotcheck.py already reads review_flags), which
        # is the only human review surface left once the approval gate is gone.
        if batch.is_unverified(spec):
            notes.review_flags = list(notes.review_flags) + [
                f"SPEC UNGROUNDED: generated from a spec whose codes failed PDF verification "
                f"— {spec.source}"]
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


def _run_one_tracked(client, spec) -> "batch.TopicResult":
    """run_one + emit start/end progress events so --status can show the live run.
    The event I/O is best-effort and never raises (see _append_progress)."""
    _append_progress({"t": "start", "topic_id": spec.topic_id,
                      "board": spec.board, "subject": spec.subject})
    res = run_one(client, spec)
    _append_progress({"t": "end", "topic_id": res.topic_id, "outcome": res.outcome,
                      "board": res.board, "subject": res.subject,
                      "elapsed_s": round(res.elapsed_s, 1)})
    return res


def run_batch(client, specs, *, jobs: int) -> "list[batch.TopicResult]":
    """Generate a list of topics: sequential (jobs<=1, unchanged behavior) or across an
    outer pool of ``jobs`` workers. In parallel mode stdout is wrapped so each worker's
    top-level lines are tagged with its topic_id. run_one never raises, so no future
    here errors."""
    if jobs <= 1:
        return [_run_one_tracked(client, s) for s in specs]
    results: "list[batch.TopicResult]" = []
    real_stdout = sys.stdout
    tagged = batch.TaggedStdout(real_stdout)
    sys.stdout = tagged

    def _work(s):
        tagged.set_tag(s.topic_id)
        return _run_one_tracked(client, s)

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


def _run_single(topic_id: str, topics, *, include_unverified: bool = False) -> int:
    if topic_id not in topics:
        _print_topics(topics)
        sys.exit(f"\nUnknown topic '{topic_id}'.")
    spec = topics[topic_id]
    if batch.is_unverified(spec) and not include_unverified:
        # This used to generate anyway, on the reasoning that an explicit single-topic
        # request WAS the human in the loop. With the human approval step gone, the
        # marker no longer means "unreviewed" — it means the codes could not be located
        # in the official PDF — so typing the id is not evidence of anything and the
        # override must be explicit.
        sys.exit(f"\n{spec.topic_id} is UNVERIFIED: {spec.source}\n"
                 f"Its codes could not be verified against the official spec PDF, so it will "
                 f"teach and cite spec points that may not exist.\n"
                 f"Re-run extraction to repair it, or force it with --include-unverified.")
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
            print(f"  ({n_unv} UNVERIFIED spec(s) skipped — they failed PDF grounding; re-run "
                  f"extraction to repair, or pass --include-unverified)")
        return 0
    if not to_gen:
        print("Nothing to generate.")
        if n_unv:
            print(f"  {n_unv} spec(s) are UNVERIFIED — they failed PDF grounding. Re-run extraction "
                  f"to repair them, or pass --include-unverified to ship them anyway.")
        if n_exist:
            print(f"  {n_exist} topic(s) already generated — pass --force to regenerate.")
        return 0

    jobs = args.jobs if args.jobs is not None else CONFIG.get("max_parallel_topics", 1)
    jobs = max(1, min(jobs, len(to_gen)))
    client = get_gemini_client()  # fail fast (clear message) if no key BEFORE we start
    print(f"generating {len(to_gen)} topic(s), --jobs {jobs} ... (progress: py -3 src/notes.py --status)")
    results = list(skipped)
    selector = {"board": args.board, "subject": args.subject, "level": args.level}
    _begin_run(to_gen, jobs=jobs, selector=selector)
    t0 = time.monotonic()
    try:
        results += run_batch(client, to_gen, jobs=jobs)
    finally:
        elapsed = time.monotonic() - t0
        _end_run()         # close the live-progress window
        flush_langfuse()   # send buffered cost/observability events before we report
        _write_manifest(results, jobs=jobs, selected=len(selected), elapsed=elapsed)
        print(batch.format_summary(results, elapsed_s=elapsed))
    return 1 if batch.any_failures(results) else 0


def _run_status(*, watch: int) -> int:
    """Print the live generation dashboard (read-only; no API key needed). ``--watch
    SECS`` refreshes it in place until Ctrl-C — a live view over SSH during a run.
    Re-discovers curriculum each render so the total climbs live during extraction too."""
    def _render() -> str:
        topics = discover_topics()
        on_disk = _existing_output_ids(CONFIG["out_dir"]) & set(topics)
        by_total: "dict[str, int]" = {}
        by_done: "dict[str, int]" = {}
        for tid, s in topics.items():
            by_total[s.subject] = by_total.get(s.subject, 0) + 1
            if tid in on_disk:
                by_done[s.subject] = by_done.get(s.subject, 0) + 1
        subject_rows = sorted((subj, by_done.get(subj, 0), by_total[subj]) for subj in by_total)
        hints = ["Logs:  tail -f the file the run was launched into (e.g. logs/gen-*.log)",
                 "Live:  py -3 src/notes.py --status --watch 5"]
        return batch.render_status(
            total_curriculum=len(topics), existing_count=len(on_disk),
            subject_rows=subject_rows, run_status=_read_json(_status_path()),
            events=_read_events(_progress_path()), now=datetime.now(timezone.utc),
            log_hints=hints)

    if watch and watch > 0:
        try:
            while True:
                sys.stdout.write("\033[2J\033[H")  # clear screen + home cursor
                print(_render())
                print(f"\n(refreshing every {watch}s — Ctrl-C to stop)")
                sys.stdout.flush()
                time.sleep(watch)
        except KeyboardInterrupt:
            print()
            return 0
    print(_render())
    return 0


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
    ap.add_argument("--status", action="store_true",
                    help="show the generation progress dashboard and exit (read-only; no key needed)")
    ap.add_argument("--watch", type=int, default=0, metavar="SECS",
                    help="with --status: refresh the dashboard every SECS (live view; Ctrl-C to stop)")
    ap.add_argument("--all", action="store_true", help="batch over every discovered topic")
    ap.add_argument("--board", help="batch filter: only this board")
    ap.add_argument("--subject", help="batch filter: only this subject")
    ap.add_argument("--level", help="batch filter: only this level")
    ap.add_argument("--force", action="store_true", help="regenerate topics whose output already exists")
    ap.add_argument("--include-unverified", action="store_true",
                    help="also generate specs that FAILED PDF grounding (default: skip). The notes "
                         "will teach and cite spec codes that could not be found in the official spec.")
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
    if args.status:
        sys.exit(_run_status(watch=args.watch))
    if not topics:
        sys.exit("No curriculum specs found in curriculum/.")

    batch_mode = args.all or any([args.board, args.subject, args.level])
    if args.topic_id and batch_mode:
        sys.exit("Give a single topic_id OR batch flags (--all/--board/--subject/--level), not both.")

    if args.topic_id:
        sys.exit(_run_single(args.topic_id, topics, include_unverified=args.include_unverified))
    if not batch_mode:
        _print_topics(topics)
        return
    sys.exit(_run_batch_mode(args, topics))


if __name__ == "__main__":
    main()
