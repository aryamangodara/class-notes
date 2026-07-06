"""Offline smoke test — exercises everything except live Gemini calls.

Stubs google.genai so it runs with no API key and no network. Validates:
curriculum JSONs load, every prompt template .format()s cleanly, the renderers
work, and ClassNotes round-trips through JSON.
"""
import os
import sys
import types as _t

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# --- stub google.genai (only referenced inside funcs we don't call here) ---
g = _t.ModuleType("google")
gg = _t.ModuleType("google.genai")
ggt = _t.ModuleType("google.genai.types")


class _Stub:
    def __init__(self, *a, **k):
        pass


gg.Client = _Stub
ggt.HttpOptions = _Stub
ggt.GenerateContentConfig = _Stub
gg.types = ggt
g.genai = gg
sys.modules.update({"google": g, "google.genai": gg, "google.genai.types": ggt})

import config  # noqa: E402
import helpers  # noqa: E402
from schemas import (  # noqa: E402
    Callout, ClassNotes, Diagram, KeyTerm, LOCoverage, NoteSection, PracticeQuestion, WorkedExample,
)

# 1. curriculum loads
topics = helpers.discover_topics()
assert topics, "no topics discovered"
print("topics discovered:", ", ".join(topics))

# 2. every prompt template formats cleanly (catches stray braces / missing keys)
spec = topics["ap-bio-cellular-respiration"]
sb = helpers._spec_block(spec)
helpers.load_prompt("outline.txt").format(house_style=config.HOUSE_STYLE, spec_block=sb)
helpers.load_prompt("write_section.txt").format(
    house_style=config.HOUSE_STYLE, spec_block=sb, heading="H", intent="I", codes="C", outline="O")
helpers.load_prompt("finalize.txt").format(house_style=config.HOUSE_STYLE, spec_block=sb, sections="S")
helpers.load_prompt("verify.txt").format(spec_block=sb, notes="N")
print("prompt formatting OK for all 4 stages")

# 3. _clean_md: literal escaped newlines/tabs become real ones; math is protected
assert helpers._clean_md("a\\nb") == "a\nb"
assert helpers._clean_md("p\\tq") == "p\tq"
_prot = helpers._clean_md("see \\(\\neq\\) and x\\nz")   # \neq inside math stays; outside \n converts
assert "\\neq" in _prot and "x\nz" in _prot
print("_clean_md OK (newlines unescaped, math spans protected)")

# 4. renderers on a stub ClassNotes — exercises every fixed bug path
notes = ClassNotes(
    topic_id=spec.topic_id, board=spec.board, subject=spec.subject, level=spec.level,
    unit=spec.unit, topic=spec.topic, learning_objectives=spec.learning_objectives,
    overview="Why respiration matters.",
    key_terms=[KeyTerm(term="ATP", definition="the cell's energy currency")],
    sections=[NoteSection(
        heading="Glycolysis", covers_objective_codes=[spec.learning_objectives[0].code],
        body="Glucose is split.\\nThe change is \\(\\Delta G < 0\\).", confidence="high",
        worked_examples=[WorkedExample(prompt="Cost of $80 of glucose?",
                                       solution="Yield \\(2\\,\\text{ATP}\\); fee $5.")],
        diagrams=[
            Diagram(caption="overview", kind="mermaid", content="graph TD; Glucose-->Pyruvate"),
            Diagram(caption="formula", kind="latex", content="y = mx + b"),
            Diagram(caption="forms", kind="latex",
                    content="\\begin{array}{|c|c|}\\hline a & b \\\\ \\hline\\end{array}"),
        ],
        callouts=[
            Callout(kind="tip", body="Read the axis labels before computing slope."),
            Callout(kind="mistake", body="Don't confuse the slope with the \\(y\\)-intercept."),
        ],
    )],
    common_misconceptions=["Respiration is not the same as breathing."],
    exam_tips=["'Explain' needs a mechanism, not just a conclusion."],
    practice_questions=[PracticeQuestion(question="Where is the ETC?", worked_solution="Inner mitochondrial membrane.")],
    summary="Aerobic respiration yields far more ATP than fermentation.",
    coverage_report=[LOCoverage(code=lo.code, covered=True, where="Glycolysis", confidence="high")
                     for lo in spec.learning_objectives],
    review_flags=[], generated_at="2026-06-24T00:00:00+00:00",
)
md = helpers.render_markdown(notes)
html = helpers.render_html(notes)
assert spec.topic in md and "## Learning objectives" in md
# A: internal objective codes are NOT in the student-facing notes
assert spec.learning_objectives[0].code not in md
# C: literal \n was unescaped to a real newline
assert "Glucose is split.\nThe change is" in md and "Glucose is split.\\nThe change" not in md
# D: currency dollar signs survive verbatim
assert "$80" in md and "$5" in md
# B: a valid latex diagram is $$-wrapped; a \hline table is fenced, never $$-wrapped
assert "$$\ny = mx + b\n$$" in md
assert "\\begin{array}" in md and "$$\n\\begin{array}" not in md
# inline \(...\) math survives _clean_md intact
assert "\\(\\Delta G < 0\\)" in md and "\\(2\\,\\text{ATP}\\)" in md
# callouts render as labelled blockquote boxes; math inside a callout survives
assert "> **💡 Quick Tip**" in md and "> **⚠️ Common Mistake**" in md
assert "\\(y\\)-intercept" in md
assert "<html" in html and "__MD_JSON__" not in html and "__TITLE__" not in html
# D: HTML uses \(...\) inline delimiters + the protect-math step; old single-$ inline gone
assert "marked.parse" in html and "mermaid" in html and "@@MATH" in html
assert "inlineMath" in html and "[['$','$']]" not in html
# callout box styling + emoji colouriser present in the HTML
assert ".callout" in html and "classList.add('callout'" in html
print(f"render OK: markdown={len(md)} chars, html={len(html)} chars")

# 4. ClassNotes round-trips through JSON
ClassNotes.model_validate_json(notes.model_dump_json())
print("json round-trip OK")

print("\nALL SMOKE CHECKS PASSED")
