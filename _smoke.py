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
    Callout, ClassNotes, Diagram, KeyTerm, LearningObjective, LOCoverage, NoteSection,
    PracticeQuestion, WorkedExample,
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
    house_style=config.HOUSE_STYLE, spec_block=sb, heading="H", intent="I", codes="C", outline="O", exam_format="F")
helpers.load_prompt("finalize.txt").format(house_style=config.HOUSE_STYLE, spec_block=sb, sections="S", worked_examples="W")
helpers.load_prompt("verify.txt").format(spec_block=sb, notes="N")
print("prompt formatting OK for all 4 stages")

# 3. _clean_md: literal escaped newlines/tabs become real ones; math is protected
assert helpers._clean_md("a\\nb") == "a\nb"
assert helpers._clean_md("p\\tq") == "p\tq"
_prot = helpers._clean_md("see \\(\\neq\\) and x\\nz")   # \neq inside math stays; outside \n converts
assert "\\neq" in _prot and "x\nz" in _prot
print("_clean_md OK (newlines unescaped, math spans protected)")

# 4. renderers on a stub ClassNotes — exercises every fixed bug path.
# Two objectives that exercise the tier guard: one whose tier repeats the level
# (must be suppressed), one with a distinct tier (must be kept).
_los = [
    LearningObjective(code="SMK-1", statement="State the first idea", tier=spec.level),
    LearningObjective(code="SMK-2", statement="State the second idea", tier="Supplement"),
]
notes = ClassNotes(
    topic_id=spec.topic_id, board=spec.board, subject=spec.subject, level=spec.level,
    unit=spec.unit, topic=spec.topic, learning_objectives=_los,
    overview="Why respiration matters.",
    key_terms=[KeyTerm(term="ATP", definition="the cell's energy currency")],
    sections=[NoteSection(
        heading="Glycolysis", covers_objective_codes=["SMK-1"],
        body="Glucose is split.\\nThe change is \\(\\Delta G < 0\\).", confidence="high",
        worked_examples=[WorkedExample(prompt="Cost of $80 of glucose?",
                                       solution="Yield \\(2\\,\\text{ATP}\\); fee $5.")],
        diagrams=[
            Diagram(caption="overview", kind="mermaid", content="graph TD\n A[Glucose (6C)] --> B[Pyruvate]"),
            Diagram(caption="formula", kind="latex", content="y = mx + b"),
            Diagram(caption="forms", kind="latex",
                    content="\\begin{array}{|c|c|}\\hline a & b \\\\ \\hline\\end{array}"),
            Diagram(caption="A leaf cross-section", kind="image",
                    content="labelled leaf cross section",
                    image_src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
                    attribution="Leaf cross-section, CC BY-SA (via Wikimedia Commons)"),
            Diagram(caption="Energy profile", kind="description",
                    content="An energy level diagram: reactants high, products low, delta H marked."),
        ],
        callouts=[
            Callout(kind="tip", body="Read the axis labels before computing slope."),
            Callout(kind="mistake", body="Don't confuse the slope with the \\(y\\)-intercept."),
        ],
        exam_tips=["For FRQs, explain the mechanism — a bare answer earns little."],
    )],
    common_misconceptions=["Respiration is not the same as breathing."],
    practice_questions=[
        PracticeQuestion(question="Where is the ETC?", difficulty="basic", marks=2,
                         worked_solution="M1: inner mitochondrial membrane. A1: on the cristae.",
                         targets_objective_codes=["SMK-1"]),
        PracticeQuestion(question="Derive the net ATP yield of aerobic respiration.",
                         difficulty="stretch", marks=6,
                         worked_solution="M1: glycolysis nets 2. M2: ... A1: ~30-32 ATP total."),
    ],
    summary="Aerobic respiration yields far more ATP than fermentation.",
    coverage_report=[LOCoverage(code=lo.code, covered=True, where="Glycolysis", confidence="high")
                     for lo in _los],
    review_flags=[], generated_at="2026-06-24T00:00:00+00:00",
)
md = helpers.render_markdown(notes)
html = helpers.render_html(notes)
assert spec.topic in md and "<summary>Learning objectives</summary>" in md
# A: objective codes stay OUT of the Learning-objectives list (statements only)...
lo_block = md.split("<summary>Learning objectives</summary>")[1].split("</details>")[0]
assert "SMK-1" not in lo_block and "SMK-2" not in lo_block
# ...but section headers DO surface their spec codes now (coverage map / rigour signal)
assert "*Spec points: SMK-1*" in md
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
# mermaid node labels with parens are quoted so Mermaid can parse them
assert 'A["Glucose (6C)"]' in md
# image diagram renders as a <figure> with the embedded data URI + attribution
assert '<figure class="note-img">' in md and "data:image/png;base64" in md
assert "<figcaption>A leaf cross-section" in md and "CC BY-SA" in md
# exam strategy is now a per-SECTION box (inside the section), no topic-level box
assert "> **🎯 Exam strategy**" in md and "explain the mechanism" in md
assert "> **🎯 AP exam strategy**" not in md and "<summary>Exam tips</summary>" not in md
# sections render as collapsible <details class="topic"> with the heading as summary
assert '<details class="topic">' in md
assert "<summary>Glycolysis</summary>" in md and "<summary>Key terms</summary>" in md
assert "<summary>Summary</summary>" in md
# pedagogy: practice questions carry a difficulty + board-appropriate marks tag —
# this stub's level is "AP", so marks render as "points", never "marks"
assert "<summary>Practice questions</summary>" in md
assert "_(basic · 2 points)_" in md and "_(stretch · 6 points)_" in md
assert "· 2 marks)" not in md
# bug 3a: the level is not repeated when the board already carries it
# (AP board "AP (College Board)" already contains the level "AP")
assert f"{spec.board} · {spec.subject} · {spec.level}" not in md
assert f"*{spec.board} · {spec.subject} — {spec.unit}*" in md
# bug 3b: a tier that merely repeats the level is suppressed; a distinct tier is kept
assert "State the first idea" in md and f"State the first idea _({spec.level})_" not in md
assert "State the second idea _(Supplement)_" in md
# bug 4: a prose "description" diagram is NOT rendered inline in the student flow;
# it is collected into the teacher/QA footer instead
assert "> **Diagram — Energy profile" not in md
assert "Illustrations to add (not shown to students)" in md and "Energy profile" in md
# bug 5: coverage / timestamp / review flags live in a collapsed teacher/QA footer,
# not as inline student-facing text; the old inline "Coverage: … Generated …" line is gone
assert "<summary>For teachers · QA" in md
assert "**Coverage:** 2/2 learning objectives." in md
assert "learning objectives. Generated" not in md
# the timestamp is rendered date-only, never the raw ISO string
assert "2026-06-24" in md and "2026-06-24T00:00:00" not in md
assert "<html" in html and "__MD_JSON__" not in html and "__TITLE__" not in html
# D: HTML uses \(...\) inline delimiters + the protect-math step; old single-$ inline gone
assert "marked.parse" in html and "mermaid" in html and "@@MATH" in html
assert "inlineMath" in html and "[['$','$']]" not in html
# callout box styling + emoji colouriser present in the HTML
assert ".callout" in html and "classList.add('callout'" in html
assert "figure.note-img" in html
assert "details.topic" in html
# html is SELF-CONTAINED: the structured JSON is embedded inline and rendered
# client-side (buildMarkdown mirrors render_markdown); no fetch, opens from disk.
assert 'id="notes-data"' in html and "buildMarkdown(" in html and "JSON.parse(" in html
assert "__DATA_JSON__" not in html and "ap-bio-cellular-respiration" in html
# "<" inside the embedded data is escaped to < so a stray </script> can't break out
assert "u003c" in html
print(f"render OK: markdown={len(md)} chars, html={len(html)} chars")

# 4. ClassNotes round-trips through JSON
ClassNotes.model_validate_json(notes.model_dump_json())
print("json round-trip OK")

print("\nALL SMOKE CHECKS PASSED")
