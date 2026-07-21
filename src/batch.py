"""Pure batch-planning logic for notes generation (genai-free, offline-testable).

`notes.py` is a thin CLI over these helpers — the same split `coverage_gate.py` has
from the pipeline. Everything here is stdlib-only and duck-typed over specs/results,
so `tests/_smoke_notes_batch.py` exercises the batch planner, the provenance gate, the
run manifest, and the parallel-output shim without a Gemini key or network.

The batch runner isolates per-topic failures into a `TopicResult` (one bad topic never
aborts the run), gates out UNVERIFIED auto-extracted specs, skips already-generated
output by default, and records a machine-readable manifest.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime


# ---------------------------------------------------------------------------
# outcome vocabulary
# ---------------------------------------------------------------------------
OUTCOME_WRITTEN = "written"
OUTCOME_SKIPPED_EXISTING = "skipped_existing"
OUTCOME_SKIPPED_UNVERIFIED = "skipped_unverified"
OUTCOME_COVERAGE_FAILED = "coverage_failed"
OUTCOME_STRUCTURAL_FAILED = "structural_failed"
OUTCOME_ERROR = "error"

# A run exits non-zero iff any topic ended in one of these. Skips never fail a run.
FAILURE_OUTCOMES = frozenset({OUTCOME_COVERAGE_FAILED, OUTCOME_STRUCTURAL_FAILED, OUTCOME_ERROR})
SKIPPED_OUTCOMES = frozenset({OUTCOME_SKIPPED_EXISTING, OUTCOME_SKIPPED_UNVERIFIED})

# Console ordering for the summary.
_OUTCOME_ORDER = (
    OUTCOME_WRITTEN, OUTCOME_SKIPPED_EXISTING, OUTCOME_SKIPPED_UNVERIFIED,
    OUTCOME_COVERAGE_FAILED, OUTCOME_STRUCTURAL_FAILED, OUTCOME_ERROR,
)


@dataclass
class TopicResult:
    topic_id: str
    outcome: str
    detail: str = ""
    board: str = ""
    subject: str = ""
    elapsed_s: float = 0.0
    output_path: str = ""


# ---------------------------------------------------------------------------
# provenance gate (the fetch -> generate trust boundary)
# ---------------------------------------------------------------------------
UNVERIFIED_MARKER = "UNVERIFIED"


def is_unverified(spec) -> bool:
    """True for a spec the CURRICULUM GATE could not verify against its source PDF.

    The marker's meaning changed when the human approval step was removed: it used to mean
    "no human has reviewed this", it now means "``spec_gate`` could not locate every code
    and evidence quote in the official PDF this spec was extracted from, after
    ``max_spec_repair_retries`` re-extractions". Either way it is the same hard gate —
    ``notes.py`` refuses to generate from it.

    Matches the exact uppercase token `stamp_extracted` writes into `source`
    (extract_specs.py) — case-sensitive, so a lowercase 'unverified' in curated prose
    never trips the gate."""
    return UNVERIFIED_MARKER in (getattr(spec, "source", "") or "")


def clear_unverified_marker(source: str, *, note: str = "Verified against the source PDF") -> str:
    """Drop the '— UNVERIFIED: …' clause a spec was stamped with, keeping the origin and
    appending `note`. Idempotent: a source without the marker is returned unchanged.
    Deterministic (the caller injects any date), so it is safe in the offline smoke."""
    s = source or ""
    if UNVERIFIED_MARKER not in s:
        return s
    head = s.split(UNVERIFIED_MARKER, 1)[0].rstrip()   # "Auto-extracted from X —"
    head = head.rstrip("—").rstrip()                   # "Auto-extracted from X"
    return f"{head} — {note}" if head else note


def set_unverified_reason(source: str, reason: str) -> str:
    """Rewrite the '— UNVERIFIED: …' clause with a machine-written reason, keeping the
    origin, so `--list` and `git diff` say WHAT failed without opening a report file.

    Preserves the exact token ``is_unverified`` matches, so rewriting a reason can NEVER
    accidentally clear the gate — asserted in the smoke test, because that would be a
    silent downgrade from 'will not generate' to 'ships ungrounded'."""
    s = (source or "").strip()
    head = s.split(UNVERIFIED_MARKER, 1)[0].rstrip().rstrip("—").rstrip() if UNVERIFIED_MARKER in s else s
    clause = f"{UNVERIFIED_MARKER}: {reason}"
    return f"{head} — {clause}" if head else clause


# ---------------------------------------------------------------------------
# selection + planning (pure; sibling of extract_specs.plan_writes)
# ---------------------------------------------------------------------------

def select_specs(specs, *, board=None, subject=None, level=None) -> list:
    """AND filter over discovered specs — case-insensitive exact match on board/subject/
    level — preserving input order. A None filter is not applied."""
    def _match(spec) -> bool:
        for want, got in ((board, getattr(spec, "board", "")),
                          (subject, getattr(spec, "subject", "")),
                          (level, getattr(spec, "level", ""))):
            if want is not None and (want or "").strip().lower() != (got or "").strip().lower():
                return False
        return True
    return [s for s in specs if _match(s)]


def plan_batch(specs, existing_ids, *, force=False, include_unverified=False, limit=None):
    """Split selected specs into (to_generate, skipped: list[TopicResult]).

    Per-spec precedence — the UNVERIFIED gate is checked FIRST so the moat metric (how
    many specs are blocked pending review) is surfaced even when stale output also exists:
      1. is_unverified and not include_unverified -> skipped_unverified
      2. topic_id in existing_ids and not force   -> skipped_existing
      3. otherwise                                -> to_generate
    `limit` truncates to_generate only (a cheap first run); skips are always fully reported.
    """
    to_generate: list = []
    skipped: "list[TopicResult]" = []
    for s in specs:
        base = dict(topic_id=s.topic_id, board=getattr(s, "board", ""), subject=getattr(s, "subject", ""))
        if is_unverified(s) and not include_unverified:
            skipped.append(TopicResult(**base, outcome=OUTCOME_SKIPPED_UNVERIFIED,
                                       detail="spec failed PDF grounding (or --include-unverified)"))
        elif s.topic_id in existing_ids and not force:
            skipped.append(TopicResult(**base, outcome=OUTCOME_SKIPPED_EXISTING,
                                       detail="output already exists (use --force to regenerate)"))
        else:
            to_generate.append(s)
    if limit is not None:
        to_generate = to_generate[:limit]
    return to_generate, skipped


# ---------------------------------------------------------------------------
# result tallies + manifest + summary
# ---------------------------------------------------------------------------

def counts_by_outcome(results) -> "dict[str, int]":
    counts: "dict[str, int]" = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    return counts


def any_failures(results) -> bool:
    """True iff any topic hit a FAILURE_OUTCOMES state. Skips never count as failures."""
    return any(r.outcome in FAILURE_OUTCOMES for r in results)


def build_manifest(results, *, meta=None) -> dict:
    """A JSON-serializable record of the run: meta + counts + per-topic outcomes."""
    return {
        "meta": dict(meta or {}),
        "counts": counts_by_outcome(results),
        "topics": [
            {"topic_id": r.topic_id, "outcome": r.outcome, "board": r.board,
             "subject": r.subject, "elapsed_s": round(r.elapsed_s, 2),
             "detail": r.detail, "output_path": r.output_path}
            for r in results
        ],
    }


def format_summary(results, *, elapsed_s: float = 0.0) -> str:
    counts = counts_by_outcome(results)
    lines = ["", f"=== batch summary: {len(results)} topic(s) in {elapsed_s:.0f}s ==="]
    for o in _OUTCOME_ORDER:
        if counts.get(o):
            lines.append(f"  {o:20s} {counts[o]}")
    fails = [r for r in results if r.outcome in FAILURE_OUTCOMES]
    if fails:
        lines.append("  failures:")
        for r in fails:
            lines.append(f"    x {r.topic_id} [{r.outcome}] {r.detail[:90]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# live progress dashboard (READ side is pure; notes.py owns the file I/O)
#
# A long batch writes two files under out/ as it runs: run-status.json (a header
# stamped at start, closed at end) and run-progress.jsonl (one start/end event per
# topic). `--status` folds them into a dashboard. The DURABLE truth is still the
# count of .v2.json on disk (crash-proof, resumable); the events add the live view
# (in-flight, rate, ETA) of the CURRENT run. Everything here is pure over parsed
# input + an injected `now`, so `_smoke_notes_batch.py` renders it without a clock.
# ---------------------------------------------------------------------------

def summarize_progress(events) -> dict:
    """Fold progress events (dicts with ``t`` = 'start' | 'end') into live tallies.
    Later events win (a topic's 'end' supersedes its 'start'); a started id with no
    end is in flight. Unknown/missing fields are tolerated (the file is append-only
    and may be mid-write)."""
    started, ended = {}, {}
    for e in events:
        tid = e.get("topic_id")
        if not tid:
            continue
        if e.get("t") == "start":
            started[tid] = e
        elif e.get("t") == "end":
            ended[tid] = e
    in_flight = [tid for tid in started if tid not in ended]
    n_written = sum(1 for e in ended.values() if e.get("outcome") == OUTCOME_WRITTEN)
    failed = [e for e in ended.values() if e.get("outcome") in FAILURE_OUTCOMES]
    return {"started": started, "ended": ended, "in_flight": in_flight,
            "n_ended": len(ended), "n_written": n_written,
            "n_failed": len(failed), "failed": failed}


def _bar(done: int, total: int, width: int = 22) -> str:
    frac = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
    filled = int(round(frac * width))
    return f"[{'#' * filled}{'-' * (width - filled)}] {frac * 100:5.1f}%"


def _fmt_dur(seconds) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s or "")
    except Exception:
        return None


def render_status(*, total_curriculum: int, existing_count: int, subject_rows,
                  run_status, events, now: datetime, log_hints=()) -> str:
    """Render the generation dashboard. ``subject_rows`` is an iterable of
    ``(subject, done, total)``; ``run_status`` is the run header dict (or None);
    ``events`` is the parsed run-progress.jsonl. ``now`` is injected (tz-aware) so
    this stays pure/testable."""
    prog = summarize_progress(events)
    L = [f"=== Class Notes — generation status @ {now.strftime('%Y-%m-%d %H:%M:%S UTC')} ==="]

    if run_status:
        started = _parse_iso(run_status.get("started_at"))
        to_gen, jobs = run_status.get("to_generate"), run_status.get("jobs")
        if run_status.get("active"):
            age = f" · started {_fmt_dur((now - started).total_seconds())} ago" if started else ""
            L.append(f"Run: ACTIVE (pid {run_status.get('pid')} on {run_status.get('host', '?')})"
                     f"{age} · --jobs {jobs} · {to_gen} to generate this run")
        else:
            ended = _parse_iso(run_status.get("ended_at"))
            fin = f" · ended {_fmt_dur((now - ended).total_seconds())} ago" if ended else ""
            L.append(f"Run: idle (last run: {to_gen} topic(s), --jobs {jobs}){fin}")
        sel = run_status.get("selector") or {}
        L.append("Target: " + (" · ".join(f"{k}={v}" for k, v in sel.items() if v) or "all topics"))
    else:
        L.append("Run: none recorded yet")

    remaining = max(0, total_curriculum - existing_count)
    L += ["", f"Curriculum: {total_curriculum} spec(s)",
          f"On disk:    {existing_count} generated · {remaining} remaining  "
          f"{_bar(existing_count, total_curriculum)}"]

    if events:
        L += ["", f"This run:   written {prog['n_written']} | failed {prog['n_failed']} | "
              f"in-flight {len(prog['in_flight'])}"]
        if prog["in_flight"]:
            more = f" (+{len(prog['in_flight']) - 6} more)" if len(prog["in_flight"]) > 6 else ""
            L.append(f"In flight:  {', '.join(prog['in_flight'][:6])}{more}")
        started = _parse_iso((run_status or {}).get("started_at"))
        if started and prog["n_ended"] > 0:
            rate = prog["n_ended"] / max(1e-6, (now - started).total_seconds() / 60.0)
            line = f"Rate:       ~{rate:.1f} topic/min"
            if isinstance(to_gen, int) and to_gen > 0 and rate > 0:
                left = max(0, to_gen - prog["n_ended"])
                line += f" · ETA ~{_fmt_dur(left / rate * 60.0)} for {left} left"
            L.append(line)
        if prog["failed"]:
            bits = ", ".join(f"{e.get('topic_id')} [{e.get('outcome', '').replace('_failed', '')}]"
                             for e in prog["failed"][-5:])
            L.append(f"Recent fail:{bits}")

    rows = [r for r in subject_rows if r[2] > 0]
    if rows:
        width = max(len(r[0]) for r in rows)
        L.append("")
        L.append("By subject:")
        for subj, done, tot in rows:
            L.append(f"  {subj:<{width}}  {done:>4}/{tot:<4}  {_bar(done, tot, 14)}")

    if log_hints:
        L.append("")
        L += list(log_hints)
    return "\n".join(L)


# ---------------------------------------------------------------------------
# parallel-output shim (used only when --jobs > 1)
# ---------------------------------------------------------------------------

class TaggedStdout:
    """Thread-safe stdout wrapper that prefixes each line with the calling thread's tag
    ('[topic_id] '). Installed by notes.py only when generating topics in parallel, so
    interleaved per-topic progress stays legible — zero changes to the pipeline's prints.
    Each worker calls set_tag(topic_id) once; an untagged thread passes through verbatim."""

    def __init__(self, sink):
        self._sink = sink
        self._local = threading.local()
        self._lock = threading.Lock()
        self._at_line_start = True

    def set_tag(self, tag: str) -> None:
        self._local.tag = tag

    def _tag(self) -> str:
        return getattr(self._local, "tag", "") or ""

    def write(self, s) -> None:
        if not s:
            return
        tag = self._tag()
        with self._lock:
            if not tag:
                self._sink.write(s)
                return
            prefix = f"[{tag}] "
            out = []
            for ch in s:
                if self._at_line_start and ch != "\n":
                    out.append(prefix)
                    self._at_line_start = False
                out.append(ch)
                if ch == "\n":
                    self._at_line_start = True
            self._sink.write("".join(out))

    def flush(self) -> None:
        with self._lock:
            self._sink.flush()
