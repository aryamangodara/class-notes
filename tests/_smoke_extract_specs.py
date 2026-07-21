"""Offline test for the spec-extraction CLI (no key/network).

The risky parts of extract_specs.py are (1) deriving a curriculum id that matches the
corpus convention so a re-extract is skipped not duplicated, (2) the skip-existing /
force / limit planner, and (3) stamping deterministic identity + UNVERIFIED provenance
onto the model's extract before it is written. All pure — verified here without a key.
"""
import os
import sys
from types import SimpleNamespace as NS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths (curriculum/ prompts/) to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))  # app modules live in src/

import extract_specs as ex  # noqa: E402
from config import board_to_level  # noqa: E402
from schemas import TopicSpec  # noqa: E402

# 1. board_to_level: exact map + best-effort fallback (never crashes).
assert board_to_level("AP (College Board)") == "AP"
assert board_to_level("Edexcel A-Level") == "A-Level"
assert board_to_level("SAT (College Board)") == "SAT"
assert board_to_level("AMC (MAA)") == "AMC 10"
assert board_to_level("Some New IGCSE Board") == "IGCSE", "fallback reads the board string"
print("board_to_level OK (mapped + fallback)")

# 2. derive_topic_id reproduces the corpus convention so re-extraction is idempotent.
assert ex.slugify("Cellular Respiration!") == "cellular-respiration"
assert ex.derive_topic_id("AP (College Board)", "Biology", "Cellular Respiration") == "ap-bio-cellular-respiration"
assert ex.derive_topic_id("Edexcel A-Level", "Mathematics", "Differentiation") == "alevel-maths-differentiation"
assert ex.derive_topic_id("Cambridge IGCSE", "Physics", "Forces & Motion") == "igcse-physics-forces-motion"
print("derive_topic_id OK (matches corpus slug convention)")

# 3. plan_writes: id-collision -> skipped (unless --force); limit caps the write list.
entries = [NS(unit="U1", topic="Alpha", keywords=["a"]),
           NS(unit="U1", topic="Beta", keywords=["b"]),
           NS(unit="U2", topic="Gamma", keywords=["g"])]
existing = {ex.derive_topic_id("AP (College Board)", "Chemistry", "Beta")}  # 'Beta' already in corpus
planned, skipped = ex.plan_writes("AP (College Board)", "Chemistry", entries, existing)
assert [t for t, _ in planned] == [ex.derive_topic_id("AP (College Board)", "Chemistry", n) for n in ("Alpha", "Gamma")]
assert [t for t, _ in skipped] == [ex.derive_topic_id("AP (College Board)", "Chemistry", "Beta")], "existing id skipped"
planned_f, skipped_f = ex.plan_writes("AP (College Board)", "Chemistry", entries, existing, force=True)
assert len(planned_f) == 3 and not skipped_f, "--force overwrites: nothing skipped"
planned_l, _ = ex.plan_writes("AP (College Board)", "Chemistry", entries, set(), limit=2)
assert len(planned_l) == 2, "--limit caps the write list"
print("plan_writes OK (skip-existing / force / limit)")

# 4. stamp_extracted: identity forced deterministically, curated layer cleared, provenance
#    stamped UNVERIFIED, and the result still validates as a TopicSpec.
raw = {  # a plausible model extract, deliberately WRONG on the controlled identity fields
    "topic_id": "model-guessed-wrong", "board": "WRONG", "subject": "WRONG", "level": "WRONG",
    "unit": "WRONG", "topic": "WRONG",
    "prerequisites": ["atoms"],
    "learning_objectives": [{"code": "1.1", "statement": "define X", "tier": None, "command_words": ["define"]}],
    "depth_profile": "some depth", "assessment_notes": "examined thus",
    "reference_data": "", "spec_checklist": [{"code": "1.1", "can_do": "I can define X", "recap": ""}],
    "exam_map": [{"key": "leak", "value": "model should not author this"}],  # must be cleared
    "next_topic": "leak", "spec_source_citation": "",
}
stamped = ex.stamp_extracted(raw, topic_id="ap-chem-thing", board="AP (College Board)", subject="Chemistry",
                             level="AP", unit="Unit 1", topic="Thing", citation="AP Chem CED")
assert stamped["topic_id"] == "ap-chem-thing" and stamped["board"] == "AP (College Board)"
assert stamped["level"] == "AP" and stamped["unit"] == "Unit 1" and stamped["topic"] == "Thing"
assert stamped["exam_map"] == [] and stamped["past_papers"] is None and stamped["next_topic"] == "", \
    "the exam-format layer must never be auto-authored by the model"
#     Default (no `gaps` passed) = NOT approved. A caller that has not run the curriculum
#     gate has not earned an approval, so the marker stays on by omission, never by luck.
assert "UNVERIFIED" in stamped["source"], "an ungated stamp stays UNVERIFIED by default"
#     The stamp must no longer instruct a HUMAN. This is the replacement for the old
#     "names ground_specs.py + git diff" assertion: nothing blocks on a person now, so a
#     provenance line telling someone to review a diff would be a lie in the git history.
assert "approve_specs" not in stamped["source"], "the stamp must not name a removed human step"
assert "git diff" not in stamped["source"], "nothing blocks on a human reading a diff"
assert stamped["spec_source_citation"] == "AP Chem CED", "citation backfilled when blank"
assert raw["board"] == "WRONG", "stamp_extracted does not mutate its input"
spec = TopicSpec.model_validate(stamped)  # the guard that runs before every write
assert spec.learning_objectives[0].code == "1.1", "extracted objective survives the round-trip"

#     With NO gaps the curriculum gate approves in place — this is the autonomous path
#     that replaced `approve_specs.py`, and the note must state the BASIS for the trust.
_ok = ex.stamp_extracted(raw, topic_id="ap-chem-thing", board="AP (College Board)", subject="Chemistry",
                         level="AP", unit="Unit 1", topic="Thing", citation="AP Chem CED",
                         gaps=[], next_topic="Next Thing")
assert "UNVERIFIED" not in _ok["source"], "a spec that passed the gate is approved in place"
assert "verified against" in _ok["source"].lower(), "the approval states what it is based on"
assert _ok["next_topic"] == "Next Thing", "next_topic is injected deterministically, not model-authored"

#     Evidence quotes are verbatim COPYRIGHTED spec text and curriculum/ is git-tracked,
#     so they must never survive the write (same rule as VerifiedPaper carrying no quote).
_with_q = dict(raw, learning_objectives=[
    {"code": "1.1", "statement": "define X", "tier": None, "command_words": ["define"],
     "evidence_quote": "candidates should be able to define X as printed in the specification"}])
_stripped = ex.stamp_extracted(_with_q, topic_id="t", board="AP (College Board)", subject="Chemistry",
                               level="AP", unit="U", topic="T", citation="C", gaps=[])
assert all("evidence_quote" not in lo for lo in _stripped["learning_objectives"]), \
    "extraction-time evidence quotes never reach curriculum/"
print("stamp_extracted OK (identity forced; gate verdict stamped; quotes stripped; validates)")

# 4b. cross-module token sync: the generation-side provenance gate (batch.is_unverified)
#     MUST recognise the exact marker extract_specs stamps, or a spec that failed PDF
#     grounding would silently ship. Guards against the token drifting between modules.
import batch  # noqa: E402
assert batch.is_unverified(NS(source=stamped["source"])), "batch.is_unverified must match the extract stamp"
assert not batch.is_unverified(NS(source=_ok["source"])), "an auto-approved spec is not gated"
assert not batch.is_unverified(NS(source=batch.clear_unverified_marker(stamped["source"]))), \
    "clearing the marker must flip the gate off"
#     Rewriting the REASON must never clear the gate — that would silently downgrade a
#     spec from "will not generate" to "ships ungrounded".
assert batch.is_unverified(NS(source=batch.set_unverified_reason(stamped["source"], "anything"))), \
    "a reason rewrite keeps the gate on"
print("cross-module token sync OK (batch gate matches the stamp; approve clears; reason rewrite cannot)")

# 5. prompt brace-safety: both extraction prompts str.format cleanly with their call-site keys.
from pathlib import Path as _Path  # noqa: E402

for _name, _keys in {
    "spec_enumerate.txt": ["board", "subject", "level"],
    "spec_extract.txt": ["board", "subject", "level", "unit", "topic", "spec_feedback"],
}.items():
    _tmpl = _Path(_ROOT, "src", "prompts", _name).read_text(encoding="utf-8")
    try:
        _tmpl.format(**{k: "x" for k in _keys})
    except (KeyError, IndexError, ValueError) as e:
        raise AssertionError(f"{_name} does not str.format cleanly with {_keys}: {e!r}")
print("prompt brace-safety OK (spec_enumerate + spec_extract format cleanly)")

print("\nALL EXTRACT-SPECS SMOKE CHECKS PASSED")
