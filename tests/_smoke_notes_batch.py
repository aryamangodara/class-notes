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


_STAMP = ("Auto-extracted from AP Chemistry CED — UNVERIFIED: run "
          "`py -3 src/ground_specs.py ap-chem-x --apply` then review `git diff` before shipping.")

# 1. is_unverified: the extract stamp trips it; curated/empty prose does not (case-sensitive).
assert batch.is_unverified(NS(source=_STAMP)) is True
assert batch.is_unverified(NS(source="Hand-seeded for POC — validate against the CED")) is False
assert batch.is_unverified(NS(source="")) is False
assert batch.is_unverified(NS(source="notes were unverified last term")) is False, "lowercase must NOT trip"
print("is_unverified OK (marker match; case-sensitive; curated/empty pass)")

# 2. clear_unverified_marker: keeps origin, drops the clause, idempotent, flips is_unverified.
cleared = batch.clear_unverified_marker(_STAMP)
assert "UNVERIFIED" not in cleared and cleared.startswith("Auto-extracted from AP Chemistry CED")
assert cleared.endswith("Verified by human review"), "review note appended"
assert batch.is_unverified(NS(source=cleared)) is False, "cleared source no longer gates"
assert batch.clear_unverified_marker(cleared) == cleared, "idempotent on an already-clean source"
assert batch.clear_unverified_marker("Hand-seeded for POC") == "Hand-seeded for POC", "no marker -> unchanged"
print("clear_unverified_marker OK (origin kept; idempotent; flips is_unverified)")

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

print("\nALL NOTES-BATCH SMOKE CHECKS PASSED")
