#!/usr/bin/env python3
"""MANUAL OVERRIDE for a spec the curriculum gate declined (standalone CLI).

This is no longer part of any automated path. Approval happens inside `extract_specs.py`:
the curriculum gate (`spec_gate.py`) locates every code and every objective's evidence
quote in the official spec PDF, re-extracts with the gaps injected when it cannot, and
approves in place when it can. Nothing waits for a person.

This tool exists for the one case that leaves behind: the gate is WRONG about a spec you
have checked yourself. Without it, a topic the grounder misjudges is permanently
unshippable and the only recovery is hand-editing JSON — strictly more human work, not
less. Reach for it after reading WHY a spec was declined (`--list` prints the reason the
gate wrote into the source line).

    py -3 src/approve_specs.py --list                    # which specs failed, and why
    py -3 src/approve_specs.py --all                     # DRY RUN — what would be approved
    py -3 src/approve_specs.py --board "AP (College Board)" --subject Chemistry --force-approve

The write flag is `--force-approve`, NOT `--apply`. That is deliberate: `deploy/run_all.sh`
used to run this as an unconditional `--apply` phase, which wiped the marker with nothing
checking anything and left 0 of 103 specs unverified — a rubber stamp wearing a gate's
name. A future copy-paste of that line now fails loudly instead of silently restoring it.

Pipeline: extract_specs --apply (extract + verify + auto-approve) -> notes.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from batch import UNVERIFIED_MARKER, clear_unverified_marker, is_unverified, select_specs
from config import CONFIG
from helpers import discover_topics


def _unverified_specs(board=None, subject=None, level=None) -> list:
    specs = select_specs(discover_topics().values(), board=board, subject=subject, level=level)
    return [s for s in specs if is_unverified(s)]


def _print_unverified(specs) -> None:
    if not specs:
        print("No UNVERIFIED specs in curriculum/ — the curriculum gate approved everything.")
        return
    print(f"Spec(s) the curriculum gate DECLINED ({len(specs)}) — they will not generate notes:")
    for s in specs:
        # Print the machine-written reason, not just the id: it is what tells you whether
        # the gate is right (a genuinely bad extraction) or wrong (worth overriding).
        reason = (s.source or "").split(UNVERIFIED_MARKER, 1)[-1].lstrip(": ").strip()
        print(f"  {s.topic_id:46s} {s.board} | {s.subject}")
        print(f"    {reason[:110]}")


def approve_one(spec) -> bool:
    """Clear the UNVERIFIED marker in the spec's curriculum JSON, in place. Returns True
    iff the file changed. Patches only the `source` field; keeps key order + unicode for
    a tight git diff (same write convention as ground_specs/extract_specs)."""
    path = Path(CONFIG["curriculum_dir"]) / f"{spec.topic_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    before = data.get("source", "")
    # Says WHO trusted it and on what basis. The automated path writes "verified against
    # the source PDF"; this one records that a human overrode a deterministic failure, so
    # the two are never confusable in a git history.
    after = clear_unverified_marker(
        before, note="Manually force-approved (overrides a failed PDF-grounding check)")
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
    ap = argparse.ArgumentParser(
        description="MANUAL OVERRIDE: force-approve a spec the curriculum gate declined.")
    ap.add_argument("--board", help="filter: only this board")
    ap.add_argument("--subject", help="filter: only this subject")
    ap.add_argument("--level", help="filter: only this level")
    ap.add_argument("--all", action="store_true", help="every declined spec")
    ap.add_argument("--list", action="store_true", help="list declined specs (with the reason) and exit")
    # NOT --apply. run_all.sh once ran this as an unconditional `--apply` phase, which
    # wiped the marker with nothing checking anything. A distinct flag makes re-collapsing
    # the gate a deliberate act rather than a copy-paste.
    ap.add_argument("--force-approve", action="store_true",
                    help="WRITE: clear the marker despite the failed grounding check. Default is dry run.")
    args = ap.parse_args()

    if args.list:
        _print_unverified(_unverified_specs())
        return

    selecting = args.all or any([args.board, args.subject, args.level])
    if not selecting:
        _print_unverified(_unverified_specs())
        print("\nSelect specs with --all or --board/--subject/--level (add --force-approve to write).")
        return

    pending = _unverified_specs(args.board, args.subject, args.level)
    if not pending:
        print("No declined specs match that selection — nothing to override.")
        return

    mode = "FORCE-APPROVE" if args.force_approve else "DRY RUN"
    print(f"[{mode}] {len(pending)} spec(s) the curriculum gate declined:")
    changed = []
    for s in pending:
        if not args.force_approve:
            print(f"  would force-approve {s.topic_id}  ({s.board} · {s.subject})")
        elif approve_one(s):
            changed.append(s.topic_id)
            print(f"  ! force-approved {s.topic_id} (overriding a failed PDF-grounding check)")
    if args.force_approve:
        print(f"\n! force-approved {len(changed)} spec(s) — now generatable, on YOUR judgement "
              f"rather than the gate's. Review with: git diff curriculum/")
    else:
        print("\nDry run — re-run with --force-approve to override the gate.")


if __name__ == "__main__":
    main()
