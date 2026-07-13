"""Offline oracle for the v2 interactive renderer.

Asserts what actually matters now that the output is interactive DOM, not a
Markdown string: schema<->dispatcher parity, template parity, progress is
derived (no hardcoded TOTAL), embedded-JSON escaping, per-block invariants
(via validate_interactives), and JSON round-trip. Dependency-free (no genai,
no Node/jsdom) so it stays the fast regression gate.
"""
import os
import re
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import render_v2  # noqa: E402
import schemas_v2 as v2  # noqa: E402


def sample(review_flags=None, bad_mcq=False):
    return v2.InteractiveNotes(
        topic_id="t", board="Edexcel A-Level", subject="Chemistry", level="A-Level",
        unit="Topic 8", topic="Enthalpy",
        hero={"eyebrow": "Edexcel", "title": "Enthalpy", "lede": "heat moves \\(x\\)", "icon": "⚡"},
        hook={"type": "reveal", "question": "Why hot?", "teaser": "click", "answer": "oxidation"},
        sections=[{
            "heading": "Defs", "spec_label": "Spec 8.1", "covers_objective_codes": ["8.1"],
            "blocks": [
                {"type": "prose", "body": "Intro \\(\\Delta H\\)"},
                {"type": "flip_cards", "title": "Cards", "cards": [{"front": "A", "back": "a"}, {"front": "B", "back": "b"}]},
                {"type": "table", "caption": "sum", "headers": ["x", "y"], "rows": [["1", "2"]]},
                {"type": "callout", "kind": "mistake", "title": "⚠️ x", "body": "watch out"},
                {"type": "toggle_diagram", "template": "energy_profile", "states": [
                    {"key": "exo", "label": "Exo", "caption": "below", "product_position": "below", "accent": "exo"},
                    {"key": "endo", "label": "Endo", "caption": "above", "product_position": "above", "accent": "endo"}]},
                {"type": "cycle_diagram", "caption": "cyc", "bottom": "Elements",
                 "edges": [{"frm": "bottom", "to": "top_left", "label": "a"}, {"frm": "bottom", "to": "top_right", "label": "b"}, {"frm": "top_left", "to": "top_right", "label": "c"}]},
                {"type": "sim", "title": "Cal",
                 "inputs": [{"key": "m", "label": "m", "unit": "g", "min": 25, "max": 200, "step": 5, "default": 100},
                            {"key": "t", "label": "dT", "unit": "K", "min": 1, "max": 40, "step": 0.5, "default": 6.5},
                            {"key": "n", "label": "n", "unit": "mol", "min": 0.01, "max": 0.2, "step": 0.005, "default": 0.05}],
                 "constants": [{"key": "c", "value": 4.18}],
                 "expression": "-(m*c*t)/1000/n", "qline_template": "q = {m} × {c} × {t} = {q} J",
                 "qline_expression": "m*c*t", "output_label": "ΔH", "output_unit": "kJ/mol",
                 "toggle": {"label": "heat loss", "factor": 0.85, "note": "~85%"}},
                {"type": "sort", "title": "Bonds", "prompt": "H–H + Cl–Cl → 2 H–Cl",
                 "buckets": [{"key": "broken", "label": "Broken", "accent": "endo"}, {"key": "made", "label": "Made", "accent": "exo"}],
                 "items": [{"label": "H–H · 436", "value": 436, "correct_bucket": "broken"},
                           {"label": "Cl–Cl · 243", "value": 243, "correct_bucket": "broken"},
                           {"label": "H–Cl · 432", "value": 432, "correct_bucket": "made"},
                           {"label": "H–Cl · 432", "value": 432, "correct_bucket": "made"}],
                 "result_expression": "sum(broken) - sum(made)"},
                {"type": "step_reveal", "tag": "Worked", "prompt": "solve", "think_hint": "which mass?",
                 "steps": [{"title": "Step 1", "body": "heat", "formula": "q = 100 × 4.18 × 6.5 = 2717 J"}]},
                {"type": "mcq", "question": "Which?", "options": [
                    {"text": "a", "correct": True, "explanation": "yes"},
                    {"text": "b", "correct": (True if bad_mcq else False), "explanation": "no"}]},
                {"type": "figure", "diagram": {"caption": "leaf", "kind": "image", "content": "leaf",
                                               "image_src": "data:image/png;base64,AAAA", "attribution": "CC0"}},
            ],
        }],
        practice=[{"type": "numeric", "label": "Q1 · basic · 2 marks", "question": "calc", "answer": -56.4,
                   "tolerance": 0.6, "unit": "kJ/mol",
                   "wrong_answers": [{"value": 56.4, "tolerance": 0.6, "message": "sign"}],
                   "mark_scheme": [{"label": "M1", "text": "q"}, {"label": "A1", "text": "ans"}]}],
        command_words=[{"word": "Define", "gloss": "recite"}],
        mistakes=[{"summary": "wrong mass", "detail": "use the solution"}],
        spec_checklist={"source_title": "Edexcel 9CH0", "source_citation": "Issue 3",
                        "items": [{"code": "8.1", "can_do": "I can define ΔH", "recap": "recap here"}]},
        review_flags=review_flags or [],
        finish={"next_topic": "Kinetics"},
    )


js = render_v2._JS

# 1. schema <-> dispatcher parity: every block type has a renderBlock case, and vice versa.
# (renderBlock cases assign `el=`; the event-delegation switch cases do not.)
cases = set(re.findall(r"case '(\w+)':\s*el=", js))
schema_types = set(v2.BLOCK_TYPES)
assert cases == schema_types, f"dispatcher/schema drift: only-JS={cases - schema_types} only-schema={schema_types - cases}"
print(f"parity OK: {len(schema_types)} block types === renderBlock cases")

# 2. SVG template parity: every schema SVG template has a JS builder.
for tmpl in v2.SVG_TEMPLATES:
    assert re.search(tmpl + r"\s*:\s*function", js), f"SVG template '{tmpl}' has no JS builder"
print(f"template parity OK: {v2.SVG_TEMPLATES}")

# 3. progress is derived, not hardcoded.
assert "PROG.total" in js and "TOTAL =" not in js and "TOTAL=" not in js, "progress must derive from block count"
print("progress-derivation OK (no hardcoded TOTAL)")

# 4. render sample; sentinels present, placeholders gone.
n = sample()
html = render_v2.render_interactive_html(n)
assert 'id="notes-data"' in html and "renderBlock(" in html and "JSON.parse(" in html
assert ".speclist" in html and ".numq" in html and ".sim-out" in html
assert "__TITLE__" not in html and "__DATA_JSON__" not in html
assert "mathjax" in html.lower() and "marked" in html
print(f"render OK: {len(html)} chars, structural markers present")

# 5. embedded-JSON escaping: a field containing </script> is neutralised.
n2 = sample()
n2.sections[0].blocks[0].body = "danger A</script><b>B"
html2 = render_v2.render_interactive_html(n2)
assert "A\\u003c/script>\\u003cb>B" in html2, "field '<' must be escaped to \\u003c"
assert "A</script><b>B" not in html2, "raw </script> from a field must not survive"
print("escaping OK (</script> in a field -> \\u003c)")

# 6. per-block invariants via validate_interactives.
good = render_v2.validate_interactives(sample())
assert good == [], f"clean note should have no flags, got: {good}"
bad = render_v2.validate_interactives(sample(bad_mcq=True))
assert any("correct options" in f for f in bad), f"two-correct MCQ should be flagged, got: {bad}"
print(f"validate_interactives OK (clean=0 flags, bad-mcq flagged: {len(bad)})")

# 7. progress ids derived + unique.
ids = render_v2.interactive_block_ids(sample())
assert ids and len(ids) == len(set(ids)), "interactive block ids must be non-empty and unique"
print(f"interactive block ids OK: {len(ids)} trackable")

# 8. JSON round-trip.
v2.InteractiveNotes.model_validate_json(n.model_dump_json())
print("json round-trip OK")

# 9. coverage-gate logic (deterministic enforcement; coverage_gate is genai-free,
#    so the hard-block safety net is exercised here without a key or network).
import coverage_gate as cg  # noqa: E402


class _Cov:  # duck-typed stand-in for LOCoverage / LearningObjective / section
    def __init__(self, code, covered=True, gap_note="", statement="", codes=None,
                 command_words=None, blocks=None):
        self.code, self.covered, self.gap_note = code, covered, gap_note
        self.statement, self.covers_objective_codes = statement, codes or []
        self.command_words = command_words or []
        self.blocks = blocks or []


items = [_Cov("8.1", True), _Cov("7.3", False, "only appears in an MCQ, never derived"), _Cov("9.9", False)]
assert [c.code for c in cg.uncovered_items(items)] == ["7.3", "9.9"], "uncovered filter"

objs = [_Cov("8.1", statement="define enthalpy change"),
        _Cov("7.3", statement="prove the derivative of cos x from first principles"),
        _Cov("9.9", statement="integrate simple polynomials")]
secs = [_Cov("s0", codes=["8.1"]), _Cov("s1", codes=["7.3"])]  # note: 9.9 claimed by NO section
texts = ["enthalpy heat energy definition", "prove derivative cos first principles"]
gaps = cg.uncovered_items(items)
targets, forced = cg.plan_regeneration(gaps, secs, texts, objs)
assert 1 in targets, "an uncovered claimed objective (7.3) must target its owning section"
assert forced, "an uncovered UNclaimed objective (9.9) must be force-routed to a section"
assert all(0 <= i < len(secs) for i in forced), "force-routed index must be a real section"
assert cg.feedback_block([]) == "", "empty feedback -> empty string (no COVERAGE FIX on a clean draft)"
assert "COVERAGE FIX" in cg.feedback_block(targets[1]), "re-draft feedback carries the fix header"
try:
    raise cg.CoverageError("demo", gaps)
except cg.CoverageError as e:
    assert "7.3" in str(e) and "9.9" in str(e), "CoverageError must name the uncovered codes"
print(f"coverage-gate OK (uncovered={len(gaps)}, targets={sorted(targets)}, forced={sorted(forced)})")

# 10. prompt brace-safety: every prompt str.format's cleanly with its call-site keys.
#     (Migrated from the removed _smoke.py; a stray literal { } once broke every
#     section-write with KeyError. Dependency-free: no helpers/genai import.)
from pathlib import Path as _Path  # noqa: E402

_PROMPT_KEYS = {
    "outline.txt": ["house_style", "spec_block"],
    "verify.txt": ["spec_block", "notes"],
    "v2_write_section.txt": ["house_style", "spec_block", "heading", "intent",
                             "codes", "outline", "exam_format", "coverage_feedback"],
    "v2_write_practice.txt": ["house_style", "spec_block", "sections", "worked_examples"],
    "v2_finalize.txt": ["house_style", "spec_block", "sections", "checklist"],
    "past_papers_candidates.txt": ["spec_block", "paper_label"],
    "past_papers_verify.txt": ["spec_block", "candidates"],
    "spec_ground.txt": ["board", "subject", "level", "unit", "topic", "items"],
}
for _name, _keys in _PROMPT_KEYS.items():
    _tmpl = _Path("prompts", _name).read_text(encoding="utf-8")
    try:
        _tmpl.format(**{k: "x" for k in _keys})
    except (KeyError, IndexError, ValueError) as e:
        raise AssertionError(f"{_name} does not str.format cleanly with {_keys}: {e!r}")
print(f"prompt brace-safety OK ({len(_PROMPT_KEYS)} prompts format cleanly)")

# 11. structural coverage gate (deterministic: command-word -> required block type).
from types import SimpleNamespace as _NS  # noqa: E402


def _blk(*type_tags):
    return [_NS(type=t) for t in type_tags]


# MOTIVATING FAILURE: a 'prove' objective assessed only by an mcq -> structural gap.
o_prove = _Cov("7.3", statement="prove d/dx cos x from first principles", command_words=["prove", "show"])
assert cg.structural_fail_codes([o_prove], [_Cov("s", codes=["7.3"], blocks=_blk("prose", "mcq"))]) == {"7.3"}
_si = cg.structural_gap_items([o_prove], [_Cov("s", codes=["7.3"], blocks=_blk("mcq"))])
assert _si and _si[0].covered is False and "step_reveal" in _si[0].gap_note
# same objective WITH a step_reveal present -> passes.
assert cg.structural_fail_codes([o_prove], [_Cov("s", codes=["7.3"], blocks=_blk("mcq", "step_reveal"))]) == set()
# 'calculate' satisfied by a numeric block; prose-only fails.
o_calc = _Cov("8.2", command_words=["calculate"])
assert cg.structural_fail_codes([o_calc], [_Cov("s", codes=["8.2"], blocks=_blk("numeric"))]) == set()
assert cg.structural_fail_codes([o_calc], [_Cov("s", codes=["8.2"], blocks=_blk("prose"))]) == {"8.2"}
# soft/empty command words never flag (model verifier's job).
_soft = [_Cov("9.9", command_words=["explain"]), _Cov("9.0", command_words=[])]
assert cg.structural_fail_codes(_soft, [_Cov("s", codes=["9.9", "9.0"], blocks=_blk("prose"))]) == set()
# multi-section union: a step_reveal in EITHER covering section -> passes.
_s1 = _Cov("s", codes=["7.3"], blocks=_blk("prose"))
_s2 = _Cov("s", codes=["7.3"], blocks=_blk("step_reveal"))
assert cg.structural_fail_codes([o_prove], [_s1, _s2]) == set()
# recall tier (define/state -> flip_cards) is OFF by default, ON via the flag.
o_def = _Cov("8.1", command_words=["define"])
_sp = _Cov("s", codes=["8.1"], blocks=_blk("prose"))
assert cg.structural_fail_codes([o_def], [_sp]) == set()
assert cg.structural_fail_codes([o_def], [_sp], include_recall=True) == {"8.1"}
# a StructuralGap flows through the SAME plan_regeneration / CoverageError path.
_gi = cg.structural_gap_items([o_prove], [_Cov("s", codes=["7.3"], blocks=_blk("mcq"))])
_t, _f = cg.plan_regeneration(_gi, [_Cov("s", codes=["7.3"], blocks=_blk("mcq"))], ["prove derivative"], [o_prove])
assert "step_reveal" in cg.feedback_block(_t[0])
try:
    raise cg.CoverageError("demo", _gi)
except cg.CoverageError as _e:
    assert "7.3" in str(_e)
print("structural-gate OK (prove-by-mcq caught; soft/empty ignored; multi-section union; recall-gated)")

# 12. exam tips are subject-aware: SAT Math keeps its Math-only facts (Desmos, grid-ins),
#     SAT Reading & Writing must NOT inherit them, and a level with no subject overlay
#     falls back to the board-general tips (unknown level -> empty, no crash).
from config import BOARD_EXAM_TIPS as _BET, exam_tips_for as _tips  # noqa: E402

_math = " ".join(_tips("SAT", "Mathematics")).lower()
_rw = " ".join(_tips("SAT", "Reading and Writing")).lower()
assert "desmos" in _math and "grid-in" in _math, "SAT Math tips must keep the calculator/grid-in facts"
assert "desmos" not in _rw and "grid-in" not in _rw, "SAT R&W must NOT inherit SAT Math-only facts"
assert set(_BET.get("SAT", [])) <= set(_tips("SAT", "Mathematics")), "general SAT tips apply to every subject"
assert _tips("AMC 10", "Mathematics") == _BET.get("AMC 10", []), "no subject overlay -> just the general tips"
assert _tips("Nope", "Nope") == [], "unknown level -> empty (no crash)"
print("exam-tips subject-aware OK (SAT Math keeps Desmos/grid-in; R&W does not; fallback clean)")

print("\nALL V2 SMOKE CHECKS PASSED")
