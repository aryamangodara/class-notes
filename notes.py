#!/usr/bin/env python3
"""Class Notes generator — POC entry point.

Feed a topic; get grounded, board-aligned class notes as json (the source of
truth) plus a self-contained html render of it.

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

from helpers import discover_topics, generate_notes, get_gemini_client, save_notes


def _print_topics(topics) -> None:
    if not topics:
        print("No curriculum specs found in curriculum/. Add a TopicSpec JSON.")
        return
    print("Seeded topics:")
    for tid, s in topics.items():
        print(f"  {tid:46s} {s.board} | {s.subject} | {s.level} - {s.topic}")


def run_one(client, spec) -> None:
    print(f"\n=== {spec.topic} ({spec.board}) ===")
    notes = generate_notes(client, spec)
    paths = save_notes(notes)
    covered = sum(1 for c in notes.coverage_report if c.covered)
    total = len(notes.coverage_report)
    print(
        f"  ✓ {covered}/{total} objectives covered | {len(notes.sections)} sections | "
        f"{len(notes.review_flags)} review flag(s)"
    )
    print(f"    {paths['json']}\n    {paths['html']}")


def run_one_v2(client, spec) -> None:
    """Generate the INTERACTIVE v2 format (block-based; self-contained interactive HTML)."""
    from pipeline_v2 import generate_interactive_notes, save_interactive_notes
    print(f"\n=== {spec.topic} ({spec.board}) · INTERACTIVE v2 ===")
    notes = generate_interactive_notes(client, spec)
    save_interactive_notes(notes)


def main() -> None:
    # Windows consoles default to cp1252; allow unicode (✓, ·, —) in our output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_dotenv()
    ap = argparse.ArgumentParser(description="Generate grounded class notes for a topic.")
    ap.add_argument("topic_id", nargs="?", help="topic id (see --list)")
    ap.add_argument("--list", action="store_true", help="list seeded topics and exit")
    ap.add_argument("--all", action="store_true", help="generate notes for all seeded topics")
    ap.add_argument("--v2", action="store_true", help="generate the INTERACTIVE v2 format")
    args = ap.parse_args()

    topics = discover_topics()
    if args.list or (not args.topic_id and not args.all):
        _print_topics(topics)
        return
    if not topics:
        sys.exit("No curriculum specs found in curriculum/.")

    client = get_gemini_client()
    runner = run_one_v2 if args.v2 else run_one
    if args.all:
        for spec in topics.values():
            runner(client, spec)
    else:
        if args.topic_id not in topics:
            _print_topics(topics)
            sys.exit(f"\nUnknown topic '{args.topic_id}'.")
        runner(client, topics[args.topic_id])


if __name__ == "__main__":
    main()
