#!/usr/bin/env python3
"""Approve auto-extracted curriculum specs after human review (standalone CLI).

`extract_specs.py` stamps every extracted spec UNVERIFIED; `ground_specs.py` verifies
its codes against the official PDF; a human reviews the `git diff`. THIS is the final
handoff — it clears the UNVERIFIED marker so `notes.py` will generate the topic (the
generator skips UNVERIFIED specs by default).

Kept separate from `ground_specs.py` on purpose: grounding is *automated* verify +
autocorrect that still leaves low-confidence items for a human, so clearing the
human-trust marker there would collapse the gate. Approval is the human review event;
the source-line rewrite shows up as an audit trail in the next `git diff`.

    py -3 src/approve_specs.py --list                    # which specs are UNVERIFIED
    py -3 src/approve_specs.py --all                     # DRY RUN — what would be approved
    py -3 src/approve_specs.py --board "AP (College Board)" --subject Chemistry --apply
    py -3 src/approve_specs.py --all --apply             # approve every reviewed spec

Grounding pipeline: extract_specs --apply -> ground_specs --apply -> git diff -> THIS --apply -> notes.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from batch import clear_unverified_marker, is_unverified, select_specs
from config import CONFIG
from helpers import discover_topics


def _unverified_specs(board=None, subject=None, level=None) -> list:
    specs = select_specs(discover_topics().values(), board=board, subject=subject, level=level)
    return [s for s in specs if is_unverified(s)]


def _print_unverified(specs) -> None:
    if not specs:
        print("No UNVERIFIED specs in curriculum/ — everything is approved.")
        return
    print(f"UNVERIFIED spec(s) awaiting approval ({len(specs)}):")
    for s in specs:
        print(f"  {s.topic_id:46s} {s.board} | {s.subject}")


def approve_one(spec) -> bool:
    """Clear the UNVERIFIED marker in the spec's curriculum JSON, in place. Returns True
    iff the file changed. Patches only the `source` field; keeps key order + unicode for
    a tight git diff (same write convention as ground_specs/extract_specs)."""
    path = Path(CONFIG["curriculum_dir"]) / f"{spec.topic_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    before = data.get("source", "")
    after = clear_unverified_marker(before)
    if after == before:
        return False
    data["source"] = after
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Approve reviewed UNVERIFIED curriculum specs.")
    ap.add_argument("--board", help="filter: only this board")
    ap.add_argument("--subject", help="filter: only this subject")
    ap.add_argument("--level", help="filter: only this level")
    ap.add_argument("--all", action="store_true", help="every UNVERIFIED spec")
    ap.add_argument("--list", action="store_true", help="list UNVERIFIED specs and exit")
    ap.add_argument("--apply", action="store_true", help="WRITE (clear the marker); default is dry run")
    args = ap.parse_args()

    if args.list:
        _print_unverified(_unverified_specs())
        return

    selecting = args.all or any([args.board, args.subject, args.level])
    if not selecting:
        _print_unverified(_unverified_specs())
        print("\nSelect specs to approve with --all or --board/--subject/--level (add --apply to write).")
        return

    pending = _unverified_specs(args.board, args.subject, args.level)
    if not pending:
        print("No UNVERIFIED specs match that selection — nothing to approve.")
        return

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] {len(pending)} UNVERIFIED spec(s):")
    changed = []
    for s in pending:
        if not args.apply:
            print(f"  would approve {s.topic_id}  ({s.board} · {s.subject})")
        elif approve_one(s):
            changed.append(s.topic_id)
            print(f"  ✓ approved {s.topic_id}")
    if args.apply:
        print(f"\n✓ approved {len(changed)} spec(s) — now generatable (e.g. py -3 src/notes.py --all).")
        if changed:
            print("  Review the source-line change with: git diff curriculum/")
    else:
        print("\nDry run — re-run with --apply to clear the UNVERIFIED marker.")


if __name__ == "__main__":
    main()
