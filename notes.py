#!/usr/bin/env python3
"""Class Notes generator — entry point.

Feed a topic; get grounded, board-aligned INTERACTIVE class notes: the block
structure as ``out/<id>.v2.json`` (the source of truth) plus a self-contained
``out/<id>.interactive.html`` that renders it client-side.

    python notes.py --list                       # show seeded topics
    python notes.py ap-bio-cellular-respiration  # generate one topic
    python notes.py --all                        # generate every seeded topic

Grounding lives in curriculum/*.json (one TopicSpec per topic). Drop a new JSON
there and it is picked up automatically — no code change needed.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from helpers import discover_topics, get_gemini_client


def _print_topics(topics) -> None:
    if not topics:
        print("No curriculum specs found in curriculum/. Add a TopicSpec JSON.")
        return
    print("Seeded topics:")
    for tid, s in topics.items():
        print(f"  {tid:46s} {s.board} | {s.subject} | {s.level} - {s.topic}")


def run_one(client, spec) -> bool:
    """Generate the interactive notes for one topic and write them.

    Returns False (writing nothing) when the coverage gate hard-fails, so a topic
    that cannot cover its contract never ships and the caller can exit non-zero.
    """
    from pipeline_v2 import generate_interactive_notes, save_interactive_notes
    from coverage_gate import CoverageError
    print(f"\n=== {spec.topic} ({spec.board}) ===")
    try:
        notes = generate_interactive_notes(client, spec)
    except CoverageError as exc:
        print(f"  ✗ COVERAGE FAILED — nothing written for {spec.topic_id}.")
        print(f"    {exc}")
        return False
    save_interactive_notes(notes)
    return True


def main() -> None:
    # Windows consoles default to cp1252; allow unicode (✓, ·, —) in our output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_dotenv()
    ap = argparse.ArgumentParser(description="Generate grounded, interactive class notes for a topic.")
    ap.add_argument("topic_id", nargs="?", help="topic id (see --list)")
    ap.add_argument("--list", action="store_true", help="list seeded topics and exit")
    ap.add_argument("--all", action="store_true", help="generate notes for all seeded topics")
    args = ap.parse_args()

    topics = discover_topics()
    if args.list or (not args.topic_id and not args.all):
        _print_topics(topics)
        return
    if not topics:
        sys.exit("No curriculum specs found in curriculum/.")

    client = get_gemini_client()
    if args.all:
        # Generate every topic; a coverage hard-fail on one does not abort the run.
        failed = [spec.topic_id for spec in topics.values() if not run_one(client, spec)]
        if failed:
            print(f"\n✗ {len(failed)} topic(s) failed the coverage gate and were NOT written: "
                  f"{', '.join(failed)}")
            sys.exit(1)
    else:
        if args.topic_id not in topics:
            _print_topics(topics)
            sys.exit(f"\nUnknown topic '{args.topic_id}'.")
        if not run_one(client, topics[args.topic_id]):
            sys.exit(1)


if __name__ == "__main__":
    main()
