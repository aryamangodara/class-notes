#!/usr/bin/env bash
# Unattended curriculum FETCH + notes GENERATION for one board (optionally one subject).
#
#   deploy/run_all.sh "<board>" [subject] [jobs]
#     deploy/run_all.sh "AP (College Board)" "Chemistry"     # one subject (pilot), jobs 3
#     deploy/run_all.sh "AP (College Board)" "Chemistry" 5   # one subject, jobs 5
#     deploy/run_all.sh "AP (College Board)"                 # whole board,  jobs 3
#     deploy/run_all.sh "AP (College Board)" 5               # whole board,  jobs 5
#
# Runs three phases non-interactively. A phase failure is LOGGED, not fatal (no set -e),
# so one bad spec/topic never aborts the run:
#   1. extract_specs <sel> --apply    official CED PDF(s) -> TopicSpecs, each held to the
#                                       CURRICULUM GATE in the same pass: every code and every
#                                       objective's evidence quote must be located in that same
#                                       PDF, else it re-extracts with the gaps injected and then
#                                       stays UNVERIFIED. Costs no extra model calls — the quotes
#                                       come back from the extraction that was already happening.
#   2. notes.py      <sel> --jobs J   generate; skips existing AND anything still UNVERIFIED,
#                                       isolates per-topic failures
#   3. spotcheck.py  --rate 10        post-hoc human sample; never blocks. Doubled from the usual
#                                       1-in-20 precisely BECAUSE the approval gate is now
#                                       automatic: it is the only human review surface left.
# where <sel> = --board "<board>" [--subject "<subject>"].
#
# There is deliberately no `approve_specs` phase. It used to run here as an unconditional
# `--apply`, which wiped the UNVERIFIED marker with nothing checking anything — a rubber
# stamp wearing a gate's name (0 of 103 specs were unverified). Approval now happens inside
# phase 1, on deterministic evidence. `approve_specs.py` survives only as a manual override
# (`--force-approve`) for a spec you have checked yourself and believe the gate got wrong.
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

# Shared-quota safety: the server's Vertex project is shared with the live Grader, so cap
# global in-flight model calls LOW by default (config.py reads this). Too high => 429
# RESOURCE_EXHAUSTED storms that also starve the Grader. Override by exporting it before launch.
export CLASSNOTES_MAX_INFLIGHT="${CLASSNOTES_MAX_INFLIGHT:-3}"
# Stream python stdout unbuffered so `tail -f` on the log is live, not chunked.
export PYTHONUNBUFFERED=1

printf 'board  : %s\nsubject: %s\njobs   : %s\ninflight: %s\npython : %s\nlog    : %s\n\n' \
  "$BOARD" "${SUBJECT:-<all subjects>}" "$JOBS" "$CLASSNOTES_MAX_INFLIGHT" "$PY" "$LOG"
printf 'watch progress : %s src/notes.py --status --watch 5\ntail logs      : tail -f %s\n\n' "$PY" "$LOG"

phase() {                                          # phase <n/N> <label> <cmd...>
  local tag="$1" label="$2"; shift 2
  echo "=== $(date -u '+%F %T') UTC  PHASE ${tag}: ${label} ===" | tee -a "$LOG"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  echo "=== $(date -u '+%F %T') UTC  PHASE ${tag} finished (exit ${rc}) ===" | tee -a "$LOG"
}

LABEL="${BOARD}${SUBJECT:+ / $SUBJECT}"
phase "1/3" "extract + verify ${LABEL}"   "$PY" src/extract_specs.py "${SEL[@]}" --apply
phase "2/3" "generate ${LABEL} j=${JOBS}" "$PY" src/notes.py "${SEL[@]}" --jobs "$JOBS"
phase "3/3" "spot-check sample"           "$PY" src/spotcheck.py --rate 10

echo "=== $(date -u '+%F %T') UTC  ALL PHASES COMPLETE ===" | tee -a "$LOG"
echo "Cost roll-up: Langfuse (per subject/stage).  Post-hoc curriculum review: git diff --stat curriculum/"
echo "Specs the gate declined (they did NOT generate): $PY src/approve_specs.py --list"
