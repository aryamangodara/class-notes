"""Offline test for the notes batch runner's pure logic (no key/network).

The risky parts of the productionized `notes.py --all` are decided in `batch.py`:
which specs are selected, which are gated out as UNVERIFIED, which are skipped as
already-generated, how failures tally into the exit code, and the run manifest. All
pure + duck-typed, so they're verified here without a Gemini key.
"""
import io
import json
import os
import sys
from types import SimpleNamespace as NS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))  # app modules live in src/

import batch  # noqa: E402


def spec(tid, board="AP (College Board)", subject="Chemistry", level="AP", source="Hand-seeded for POC"):
    return NS(topic_id=tid, board=board, subject=subject, level=level, source=source)


# What extract_specs stamps on a spec the CURRICULUM GATE could not verify. The marker's
# meaning changed with the removal of the human approval step — it no longer means "nobody
# has looked", it means "codes/quotes could not be located in the official PDF" — but the
# TOKEN is unchanged, because the gate it drives is the same one.
_STAMP = ("Auto-extracted from AP Chemistry CED — UNVERIFIED: could not be verified against "
          "the source PDF (2x code-absent (1.7.A.1, 1.7.A.2)). It will NOT generate notes.")

# 1. is_unverified: the extract stamp trips it; curated/empty prose does not (case-sensitive).
assert batch.is_unverified(NS(source=_STAMP)) is True
assert batch.is_unverified(NS(source="Hand-seeded for POC — validate against the CED")) is False
assert batch.is_unverified(NS(source="")) is False
assert batch.is_unverified(NS(source="notes were unverified last term")) is False, "lowercase must NOT trip"
print("is_unverified OK (marker match; case-sensitive; curated/empty pass)")

# 2. clear_unverified_marker: keeps origin, drops the clause, idempotent, flips is_unverified.
cleared = batch.clear_unverified_marker(_STAMP)
assert "UNVERIFIED" not in cleared and cleared.startswith("Auto-extracted from AP Chemistry CED")
assert cleared.endswith("Verified against the source PDF"), "default note states the BASIS, not who looked"
assert batch.clear_unverified_marker(_STAMP, note="Auto-approved: 7 objective(s) verified") \
    .endswith("Auto-approved: 7 objective(s) verified"), "the caller injects the real note"
assert batch.is_unverified(NS(source=cleared)) is False, "cleared source no longer gates"
assert batch.clear_unverified_marker(cleared) == cleared, "idempotent on an already-clean source"
assert batch.clear_unverified_marker("Hand-seeded for POC") == "Hand-seeded for POC", "no marker -> unchanged"
print("clear_unverified_marker OK (origin kept; idempotent; flips is_unverified)")

# 2b. set_unverified_reason: the marker is SELF-DESCRIBING, so `--list` and `git diff` say
#     what failed. Critically it must preserve the token — rewriting a reason can never be
#     a back door that downgrades "will not generate" into "ships ungrounded".
_re = batch.set_unverified_reason(_STAMP, "1x quote-absent (8.4)")
assert batch.is_unverified(NS(source=_re)), "a rewritten reason keeps the gate on"
assert "quote-absent (8.4)" in _re and "code-absent" not in _re, "the reason is replaced, not appended"
assert _re.startswith("Auto-extracted from AP Chemistry CED"), "the origin survives"
assert batch.is_unverified(NS(source=batch.set_unverified_reason("Hand-seeded", "x"))), \
    "a clean source can be gated by giving it a reason"
print("set_unverified_reason OK (self-describing; cannot clear the gate)")

# 3. select_specs: AND filters, case-insensitive, order-preserving, zero-match -> [].
pool = [
    spec("ap-chem-a", "AP (College Board)", "Chemistry", "AP"),
    spec("ap-bio-b", "AP (College Board)", "Biology", "AP"),
    spec("igcse-chem-c", "Cambridge IGCSE", "Chemistry", "IGCSE"),
]
assert [s.topic_id for s in batch.select_specs(pool, board="AP (College Board)")] == ["ap-chem-a", "ap-bio-b"]
assert [s.topic_id for s in batch.select_specs(pool, subject="chemistry")] == ["ap-chem-a", "igcse-chem-c"], "case-insensitive"
assert [s.topic_id for s in batch.select_specs(pool, board="AP (College Board)", subject="Chemistry")] == ["ap-chem-a"], "AND"
assert [s.topic_id for s in batch.select_specs(pool, level="IGCSE")] == ["igcse-chem-c"]
assert batch.select_specs(pool) == pool, "no filter -> identity (order preserved)"
assert batch.select_specs(pool, board="Nope") == [], "zero match -> []"
print("select_specs OK (AND; case-insensitive; identity; zero-match)")

# 4. plan_batch: precedence (unverified > existing), force/include_unverified/limit semantics.
specs = [spec("keep"), spec("done"), spec("unv", source=_STAMP), spec("unv-done", source=_STAMP)]
existing = {"done", "unv-done"}
to_gen, skipped = batch.plan_batch(specs, existing)
assert [s.topic_id for s in to_gen] == ["keep"], "only the fresh, verified, non-existing spec generates"
by_id = {r.topic_id: r.outcome for r in skipped}
assert by_id == {"done": batch.OUTCOME_SKIPPED_EXISTING,
                 "unv": batch.OUTCOME_SKIPPED_UNVERIFIED,
                 "unv-done": batch.OUTCOME_SKIPPED_UNVERIFIED}, "unverified gate wins over existing"

# force alone does NOT bypass the provenance gate.
to_gen_f, skipped_f = batch.plan_batch(specs, existing, force=True)
assert {s.topic_id for s in to_gen_f} == {"keep", "done"}, "force regenerates existing verified..."
assert {r.topic_id for r in skipped_f} == {"unv", "unv-done"}, "...but unverified still gated by force alone"

# include_unverified admits unverified, but an existing one still needs force.
to_gen_i, skipped_i = batch.plan_batch(specs, existing, include_unverified=True)
assert {s.topic_id for s in to_gen_i} == {"keep", "unv"}, "include_unverified admits the fresh unverified spec"
assert {r.topic_id for r in skipped_i} == {"done", "unv-done"}, "existing (incl. unverified+existing) still skipped"

# force + include_unverified -> everything generates.
to_gen_all, skipped_all = batch.plan_batch(specs, existing, force=True, include_unverified=True)
assert len(to_gen_all) == 4 and not skipped_all, "force + include_unverified generates all"

# limit truncates to_generate only; skips still fully reported.
to_gen_l, skipped_l = batch.plan_batch(specs, existing, force=True, include_unverified=True, limit=2)
assert len(to_gen_l) == 2 and len(skipped_l) == 0, "limit caps to_generate, skips unaffected"
print("plan_batch OK (precedence unverified>existing; force; include_unverified; limit)")

# 5. counts_by_outcome / any_failures: skips are not failures; a failure flips the exit.
results = [
    batch.TopicResult("a", batch.OUTCOME_WRITTEN),
    batch.TopicResult("b", batch.OUTCOME_SKIPPED_EXISTING),
    batch.TopicResult("c", batch.OUTCOME_SKIPPED_UNVERIFIED),
]
assert batch.any_failures(results) is False, "written + skips -> not a failure run"
assert batch.counts_by_outcome(results)[batch.OUTCOME_WRITTEN] == 1
results.append(batch.TopicResult("d", batch.OUTCOME_STRUCTURAL_FAILED, detail="numeric Q5 no mark scheme"))
assert batch.any_failures(results) is True, "a structural failure flips any_failures"
print("counts/any_failures OK (skips are not failures; failure flips exit)")

# 6. build_manifest is JSON-serializable and complete.
manifest = batch.build_manifest(results, meta={"jobs": 3, "selected": 4})
assert manifest["counts"][batch.OUTCOME_WRITTEN] == 1 and manifest["meta"]["jobs"] == 3
assert len(manifest["topics"]) == len(results)
json.loads(json.dumps(manifest))  # round-trips
assert "structural_failed" in batch.format_summary(results, elapsed_s=12.0), "summary lists the failure class"
print("build_manifest/format_summary OK (serializable; summary lists failures)")

# 7. TaggedStdout: tagged writes are line-prefixed; untagged pass through.
sink = io.StringIO()
tagged = batch.TaggedStdout(sink)
tagged.set_tag("ap-chem-x")
tagged.write("hello\n")
tagged.write("world\n")
assert sink.getvalue() == "[ap-chem-x] hello\n[ap-chem-x] world\n", "each line gets the topic tag"
sink2 = io.StringIO()
untagged = batch.TaggedStdout(sink2)
untagged.write("no tag here\n")
assert sink2.getvalue() == "no tag here\n", "untagged thread passes through verbatim"
print("TaggedStdout OK (per-line tag; untagged passthrough)")

# 8. summarize_progress / render_status: the live dashboard folds start/end events and
#    renders purely from an injected `now` (no wall clock), so --status is testable offline.
from datetime import datetime, timezone  # noqa: E402
ev = [
    {"t": "start", "topic_id": "a", "subject": "Chemistry"},
    {"t": "end", "topic_id": "a", "subject": "Chemistry", "outcome": batch.OUTCOME_WRITTEN},
    {"t": "start", "topic_id": "b", "subject": "Biology"},          # in flight (no end)
    {"t": "start", "topic_id": "c", "subject": "Physics"},
    {"t": "end", "topic_id": "c", "subject": "Physics", "outcome": batch.OUTCOME_ERROR},
]
prog = batch.summarize_progress(ev)
assert prog["n_written"] == 1 and prog["n_failed"] == 1, "one written, one failed"
assert prog["in_flight"] == ["b"], "a started-but-not-ended topic is in flight"
assert prog["n_ended"] == 2
# a later 'end' supersedes an earlier 'start' for the same id (fold, not double-count)
assert batch.summarize_progress([{"t": "start", "topic_id": "x"},
                                 {"t": "end", "topic_id": "x", "outcome": batch.OUTCOME_WRITTEN}]
                                )["in_flight"] == [], "end clears in-flight"

now = datetime(2026, 7, 15, 10, 32, 0, tzinfo=timezone.utc)
rs = {"active": True, "pid": 123, "host": "srv", "jobs": 3, "to_generate": 10,
      "started_at": "2026-07-15T10:02:00+00:00", "ended_at": None,
      "selector": {"board": "AP (College Board)", "subject": None, "level": None}}
dash = batch.render_status(total_curriculum=100, existing_count=42,
                           subject_rows=[("Biology", 1, 10), ("Chemistry", 5, 20)],
                           run_status=rs, events=ev, now=now,
                           log_hints=["Logs:  tail -f logs/gen.log"])
assert "ACTIVE (pid 123 on srv)" in dash, "active run header"
assert "42 generated · 58 remaining" in dash, "durable on-disk progress"
assert "in-flight 1" in dash and "In flight:  b" in dash, "live in-flight from events"
assert "ETA" in dash and "topic/min" in dash, "rate + ETA from run header + events"
assert "board=AP (College Board)" in dash and "subject=" not in dash, "selector drops None filters"
assert "By subject:" in dash and "Chemistry" in dash, "per-subject breakdown"
assert "Logs:  tail -f" in dash, "log hint surfaced"
# empty/no-run states must not crash
assert "none recorded" in batch.render_status(total_curriculum=5, existing_count=0, subject_rows=[],
                                              run_status=None, events=[], now=now)
idle = batch.render_status(total_curriculum=5, existing_count=5, subject_rows=[("Maths", 5, 5)],
                           run_status={"active": False, "to_generate": 5, "jobs": 2,
                                       "started_at": "2026-07-15T09:00:00+00:00",
                                       "ended_at": "2026-07-15T09:40:00+00:00", "selector": {}},
                           events=[], now=now)
assert "idle (last run: 5" in idle and "Target: all topics" in idle, "idle run + empty selector"
print("summarize_progress/render_status OK (folds events; pure dashboard; empty/idle-safe)")

print("\nALL NOTES-BATCH SMOKE CHECKS PASSED")
