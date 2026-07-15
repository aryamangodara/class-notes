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
    """True for an auto-extracted spec not yet cleared by human review. Matches the
    exact uppercase token `stamp_extracted` writes into `source` (extract_specs.py) —
    case-sensitive, so a lowercase 'unverified' in curated prose never trips the gate."""
    return UNVERIFIED_MARKER in (getattr(spec, "source", "") or "")


def clear_unverified_marker(source: str, *, note: str = "Verified by human review") -> str:
    """Drop the '— UNVERIFIED: …' clause a spec was stamped with, keeping the origin and
    appending `note`. Idempotent: a source without the marker is returned unchanged.
    Deterministic (the caller injects any date), so it is safe in the offline smoke."""
    s = source or ""
    if UNVERIFIED_MARKER not in s:
        return s
    head = s.split(UNVERIFIED_MARKER, 1)[0].rstrip()   # "Auto-extracted from X —"
    head = head.rstrip("—").rstrip()                   # "Auto-extracted from X"
    return f"{head} — {note}" if head else note


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
                                       detail="auto-extracted; run approve_specs after review (or --include-unverified)"))
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
