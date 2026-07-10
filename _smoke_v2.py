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

print("\nALL V2 SMOKE CHECKS PASSED")
