"""Offline test for the spec-grounding apply logic (no key/network).

The risky part of ground_specs.py is mutating curriculum JSON, so the confidence
gating and in-place patch are pure functions verified here without a Gemini key.
"""
import copy
import os
from types import SimpleNamespace as NS

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import ground_specs as gs  # noqa: E402
from schemas import SpecGroundingReport, SpecItemVerdict  # noqa: E402

# 1. plan_changes: only high-confidence 'corrected' auto-applies.
report = SpecGroundingReport(items=[
    SpecItemVerdict(kind="objective", given_code="7.3", status="confirmed", confidence="high"),
    SpecItemVerdict(kind="objective", given_code="8.1", status="corrected",
                    corrected_code="8.1a", corrected_text="new text", confidence="high"),
    SpecItemVerdict(kind="checklist", given_code="8.2", status="corrected",
                    corrected_text="tweaked", confidence="medium"),
    SpecItemVerdict(kind="objective", given_code="9.9", status="absent", confidence="low"),
])
auto, attention = gs.plan_changes(report, "high")
assert [v.given_code for v in auto] == ["8.1"], "only high-confidence corrected auto-applies"
assert {v.given_code for v in attention} == {"8.2", "9.9"}, "medium-corrected + absent need review"
print("plan_changes OK (high-conf applies; medium + absent -> review)")

# 2. apply_to_spec_dict: patch code + text in place; untouched items + key order preserved.
spec_dict = {
    "learning_objectives": [
        {"code": "8.1", "statement": "old ΔH text", "tier": None, "command_words": ["define"]},
        {"code": "8.4", "statement": "unchanged", "tier": None, "command_words": []},
    ],
    "spec_checklist": [{"code": "8.2", "can_do": "old", "recap": ""}],
}
before = copy.deepcopy(spec_dict)
changed = gs.apply_to_spec_dict(spec_dict, auto)
assert len(changed) == 1, "one correction applied"
lo0 = spec_dict["learning_objectives"][0]
assert lo0["code"] == "8.1a" and lo0["statement"] == "new text", "code + text corrected in place"
assert list(lo0.keys()) == list(before["learning_objectives"][0].keys()), "key order preserved (tight diff)"
assert spec_dict["learning_objectives"][1] == before["learning_objectives"][1], "untouched objective unchanged"
assert spec_dict["spec_checklist"][0]["can_do"] == "old", "checklist untouched (8.2 was medium, not auto)"
print("apply_to_spec_dict OK (in-place; key order + unrelated items preserved)")

# 3. derive_keywords pulls topic/unit/objective words.
spec = NS(topic="Enthalpy changes", unit="Energetics",
          learning_objectives=[NS(statement="calculate mean bond enthalpies")])
kws = gs.derive_keywords(spec)
assert "Enthalpy" in kws and "bond" in kws, "keywords include topic + objective words"
print("derive_keywords OK")

print("\nALL GROUND-SPECS SMOKE CHECKS PASSED")
