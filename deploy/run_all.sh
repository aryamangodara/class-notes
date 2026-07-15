#!/usr/bin/env bash
# Unattended curriculum FETCH + notes GENERATION for one board (optionally one subject).
#
#   deploy/run_all.sh "<board>" [subject] [jobs]
#     deploy/run_all.sh "AP (College Board)" "Chemistry"     # one subject (pilot), jobs 3
#     deploy/run_all.sh "AP (College Board)" "Chemistry" 5   # one subject, jobs 5
#     deploy/run_all.sh "AP (College Board)"                 # whole board,  jobs 3
#     deploy/run_all.sh "AP (College Board)" 5               # whole board,  jobs 5
#
# Runs four phases non-interactively. A phase failure is LOGGED, not fatal (no set -e),
# so one bad spec/topic never aborts the run:
#   1. extract_specs <sel> --apply    official CED PDF(s) -> grounded TopicSpecs (UNVERIFIED)
#   2. ground_specs  --all  --apply    verify spec codes vs the SAME PDFs; auto-correct (corpus-wide;
#                                       the CLI has no subject filter — harmless re-check of others)
#   3. approve_specs <sel> --apply    auto-clear UNVERIFIED  (the human git-diff review is post-hoc)
#   4. notes.py      <sel> --jobs J   generate; skips existing, isolates per-topic failures
# where <sel> = --board "<board>" [--subject "<subject>"].
#
# All output tee'd to logs/run-<ts>.log. Auth + Langfuse come from .env (see README). Watch live:
#   <python> src/notes.py --status --watch 5      # progress dashboard
#   tail -f logs/run-<ts>.log                      # detailed log
set -uo pipefail

BOARD="${1:?usage: deploy/run_all.sh \"<board>\" [subject] [jobs]}"
# $2 numeric => it's JOBS and there is no subject (whole board); else $2 is the subject.
if [[ "${2:-}" =~ ^[0-9]+$ ]]; then SUBJECT=""; JOBS="${2}"; else SUBJECT="${2:-}"; JOBS="${3:-3}"; fi
SEL=(--board "$BOARD"); [ -n "$SUBJECT" ] && SEL+=(--subject "$SUBJECT")

cd "$(dirname "$0")/.." || exit 1                 # repo root, regardless of caller's cwd
PY="python3"; [ -x .venv/bin/python ] && PY=".venv/bin/python"
mkdir -p logs
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG="logs/run-${TS}.log"

printf 'board  : %s\nsubject: %s\njobs   : %s\npython : %s\nlog    : %s\n\n' \
  "$BOARD" "${SUBJECT:-<all subjects>}" "$JOBS" "$PY" "$LOG"
printf 'watch progress : %s src/notes.py --status --watch 5\ntail logs      : tail -f %s\n\n' "$PY" "$LOG"

phase() {                                          # phase <n/N> <label> <cmd...>
  local tag="$1" label="$2"; shift 2
  echo "=== $(date -u '+%F %T') UTC  PHASE ${tag}: ${label} ===" | tee -a "$LOG"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  echo "=== $(date -u '+%F %T') UTC  PHASE ${tag} finished (exit ${rc}) ===" | tee -a "$LOG"
}

LABEL="${BOARD}${SUBJECT:+ / $SUBJECT}"
phase "1/4" "extract ${LABEL}"          "$PY" src/extract_specs.py "${SEL[@]}" --apply
phase "2/4" "ground corpus"             "$PY" src/ground_specs.py --all --apply
phase "3/4" "approve (auto) ${LABEL}"   "$PY" src/approve_specs.py "${SEL[@]}" --apply
phase "4/4" "generate ${LABEL} j=${JOBS}" "$PY" src/notes.py "${SEL[@]}" --jobs "$JOBS"

echo "=== $(date -u '+%F %T') UTC  ALL PHASES COMPLETE ===" | tee -a "$LOG"
echo "Cost roll-up: Langfuse (per subject/stage).  Post-hoc curriculum review: git diff --stat curriculum/"
