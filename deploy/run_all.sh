#!/usr/bin/env bash
# Unattended full-board curriculum FETCH + notes GENERATION, one continuous run.
#
#   deploy/run_all.sh "AP (College Board)" [jobs]
#
# Runs four phases non-interactively. A phase failure is LOGGED, not fatal (no set -e),
# so one bad subject/spec never aborts the whole overnight run:
#
#   1. extract_specs --board <B> --apply   official CED PDFs -> grounded TopicSpecs (UNVERIFIED)
#   2. ground_specs  --all --apply          verify spec codes vs the SAME PDFs; auto-correct
#   3. approve_specs --board <B> --apply    auto-clear UNVERIFIED  (the "fully unattended" choice;
#                                           the human git-diff review becomes post-hoc)
#   4. notes.py      --board <B> --jobs J   generate; skips existing, isolates per-topic failures
#
# All output is tee'd to logs/run-<ts>.log. Auth + Langfuse come from .env (see README).
# Watch it live (from any shell on the box):
#   <python> src/notes.py --status --watch 5      # progress dashboard
#   tail -f logs/run-<ts>.log                      # detailed log
set -uo pipefail

BOARD="${1:?usage: deploy/run_all.sh \"<board>\" [jobs]}"
JOBS="${2:-3}"

cd "$(dirname "$0")/.." || exit 1                 # repo root, regardless of caller's cwd
PY="python3"; [ -x .venv/bin/python ] && PY=".venv/bin/python"
mkdir -p logs
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG="logs/run-${TS}.log"

printf 'board : %s\njobs  : %s\npython: %s\nlog   : %s\n\n' "$BOARD" "$JOBS" "$PY" "$LOG"
printf 'watch progress : %s src/notes.py --status --watch 5\ntail logs      : tail -f %s\n\n' "$PY" "$LOG"

phase() {                                          # phase <n/N> <label> <cmd...>
  local tag="$1" label="$2"; shift 2
  echo "=== $(date -u '+%F %T') UTC  PHASE ${tag}: ${label} ===" | tee -a "$LOG"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  echo "=== $(date -u '+%F %T') UTC  PHASE ${tag} finished (exit ${rc}) ===" | tee -a "$LOG"
}

phase "1/4" "extract ${BOARD}"          "$PY" src/extract_specs.py --board "$BOARD" --apply
phase "2/4" "ground corpus"             "$PY" src/ground_specs.py --all --apply
phase "3/4" "approve (auto) ${BOARD}"   "$PY" src/approve_specs.py --board "$BOARD" --apply
phase "4/4" "generate ${BOARD} j=${JOBS}" "$PY" src/notes.py --board "$BOARD" --jobs "$JOBS"

echo "=== $(date -u '+%F %T') UTC  ALL PHASES COMPLETE ===" | tee -a "$LOG"
echo "Cost roll-up: Langfuse (per subject/stage).  Post-hoc curriculum review: git diff --stat curriculum/"
