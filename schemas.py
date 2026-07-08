"""Pydantic schemas for the Class Notes generator.

Like the Grader, these types do double duty: application data classes AND
Gemini ``response_schema`` definitions for structured output. Field
descriptions are surfaced to the model and materially affect output quality,
so keep them precise.

Two families:
  * Curriculum side (grounding) — ``TopicSpec`` / ``LearningObjective``: what a
    given board+level actually requires for a topic. Hand-seeded in
    ``curriculum/*.json`` for the POC; extractable from official spec PDFs via
    ``prompts/spec_extract.txt`` for production.
  * Notes side (output) — ``ClassNotes`` and its parts: a consistent
    pedagogical template so every topic's notes share one shape.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Curriculum (the grounding / contract)
# ---------------------------------------------------------------------------

class LearningObjective(BaseModel):
    code: str = Field(
        description="Stable identifier from the official spec, e.g. 'ENE-1.J' (AP), "
        "'6.7S' (IGCSE Supplement), 'SAT-ALG-LIN2-2', or '7.3' (A-Level)."
    )
    statement: str = Field(
        description="What the student must know or be able to do, phrased as in the syllabus."
    )
    tier: str | None = Field(
        default=None,
        description="Optional depth tag, e.g. 'Core' or 'Supplement' (IGCSE) or an "
        "assessment objective. None when the board does not tier.",
    )
    command_words: list[str] = Field(
        default_factory=list,
        description="Exam command verbs this objective is assessed with, e.g. "
        "['describe','explain','prove']. Drives depth and assessment alignment.",
    )


class TopicSpec(BaseModel):
    topic_id: str = Field(
        description="Filename-safe slug, unique across boards, e.g. 'ap-bio-cellular-respiration'."
    )
    board: str = Field(
        description="Exam board / programme, e.g. 'AP (College Board)', 'Cambridge IGCSE', "
        "'SAT (College Board)', 'Edexcel A-Level'."
    )
    subject: str = Field(description="Subject, e.g. 'Biology', 'Mathematics'.")
    level: str = Field(description="Level label, e.g. 'AP', 'IGCSE', 'SAT', 'A-Level'.")
    unit: str = Field(description="Unit/theme the topic sits in, e.g. 'Unit 3: Cellular Energetics'.")
    topic: str = Field(description="Human topic title, e.g. 'Cellular Respiration'.")
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Assumed prior knowledge the notes may build on without re-teaching.",
    )
    learning_objectives: list[LearningObjective] = Field(
        description="The exact spec points this topic must cover — the contract the notes "
        "are checked against. Cover all; exceed none."
    )
    depth_profile: str = Field(
        description="Prose describing the expected depth/rigour at THIS board+level: how far "
        "to go, what to include, and what to deliberately leave out. The calibration knob that "
        "makes IGCSE != AP != A-Level for the same idea."
    )
    assessment_notes: str = Field(
        description="How the topic is examined at this board: question styles, command words, "
        "mark-scheme expectations, and common pitfalls examiners penalise."
    )
    source: str = Field(
        default="hand-seeded for POC",
        description="Provenance of this spec, e.g. 'AP Biology CED 2020, Topic 3.6' or "
        "'Cambridge 0610 syllabus 2023-2025, Topic 6'.",
    )


# ---------------------------------------------------------------------------
# Outline (planning stage)
# ---------------------------------------------------------------------------

class OutlineSection(BaseModel):
    heading: str = Field(description="Section title.")
    covers_objective_codes: list[str] = Field(
        description="Objective codes (from the TopicSpec) this section will teach. "
        "Every objective must be claimed by at least one section."
    )
    intent: str = Field(description="One sentence: what this section explains, and to what depth.")


class NotesOutline(BaseModel):
    sections: list[OutlineSection] = Field(
        description="Ordered teaching sequence: prerequisites first, collectively covering "
        "every learning objective."
    )


# ---------------------------------------------------------------------------
# Notes (output)
# ---------------------------------------------------------------------------

class KeyTerm(BaseModel):
    term: str
    definition: str = Field(
        description="Precise, level-appropriate definition. Use the board's preferred wording "
        "where it matters for marks."
    )


class WorkedExample(BaseModel):
    prompt: str = Field(description="The example question or scenario.")
    solution: str = Field(
        description="Step-by-step worked solution. Inline maths as \\(...\\), display as $$...$$. "
        "Currency as a plain $ (e.g. $80), never inside maths."
    )


class Diagram(BaseModel):
    caption: str
    kind: Literal["mermaid", "latex", "image", "description"] = Field(
        description="'mermaid' for flow/cycle/process diagrams; 'latex' for a SINGLE "
        "mathematical expression or equation ONLY (never a table — MathJax cannot render "
        "tabular/array/hline; put tables as Markdown tables in the section body); 'image' to "
        "fetch a real labelled diagram, photo, micrograph, or map from a free image library "
        "(set content to a precise search query); 'description' for a prose placeholder a "
        "teacher or illustrator fills in."
    )
    content: str = Field(
        description="The Mermaid source, a single LaTeX expression, an image SEARCH QUERY (for "
        "kind 'image', e.g. 'labelled diagram of chloroplast structure'), or a prose "
        "description per `kind`. For 'latex': one expression only, no tabular/array/hline."
    )
    image_src: str = Field(
        default="",
        description="Populated automatically after image search (base64 data URI) — leave empty.",
    )
    attribution: str = Field(
        default="",
        description="Populated automatically (image credit and licence) — leave empty.",
    )


class ImageChoice(BaseModel):
    """Gemini's pick among candidate images for one image slot."""
    choice: int = Field(description="1-based index of the best image, or 0 if none are suitable.")
    reason: str = Field(default="", description="Brief reason for the choice.")


class Callout(BaseModel):
    kind: Literal["tip", "mistake", "formula", "remember"] = Field(
        description="'tip' = a Quick Tip (an exam technique or shortcut); 'mistake' = a Common "
        "Mistake (a frequent student error AND its correction); 'formula' = a Key Formula or "
        "must-know fact (a formula for maths/science, a key fact for humanities); 'remember' = a "
        "memory aid or mnemonic. Only include a callout that is accurate and grounded in the "
        "objectives/assessment notes — omit rather than invent."
    )
    title: str = Field(default="", description="Optional short custom title; a default label is used if empty.")
    body: str = Field(
        description="1-3 sentences, Markdown. Inline maths as \\(...\\). Grounded in the "
        "material — no invented facts."
    )


class NoteSection(BaseModel):
    heading: str
    covers_objective_codes: list[str] = Field(
        description="Objective codes this section actually taught (echo of the plan, corrected "
        "if the draft drifted)."
    )
    body: str = Field(
        description="Teaching content in Markdown with real line breaks (not the literal "
        "characters backslash-n). Inline maths as \\(...\\), display as $$...$$. Currency as a "
        "plain $ (e.g. $80), never inside maths. Tables as Markdown tables, not LaTeX."
    )
    worked_examples: list[WorkedExample] = Field(default_factory=list)
    diagrams: list[Diagram] = Field(default_factory=list)
    callouts: list[Callout] = Field(
        default_factory=list,
        description="Contextual callout boxes for THIS section (Quick Tips, Common Mistakes, "
        "Key Formulas/Facts, Remember/mnemonics) — include only where they genuinely help and "
        "are grounded; do not force one of every kind.",
    )
    exam_tips: list[str] = Field(
        default_factory=list,
        description="1-3 exam-strategy pointers SPECIFIC to this section: how this part is "
        "examined and where marks are won or lost (command words, mark-scheme quirks, common "
        "errors). Grounded in the board's exam format and assessment notes; omit if the section "
        "has no distinctive exam angle.",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Honest self-reported confidence in the factual accuracy of this section."
    )


class PracticeQuestion(BaseModel):
    question: str
    worked_solution: str = Field(
        description="Full solution, not just the final answer, so the notes are self-teaching."
    )
    targets_objective_codes: list[str] = Field(default_factory=list)


class NotesExtras(BaseModel):
    """The pedagogical scaffolding assembled once over all section drafts."""
    overview: str = Field(description="Short orientation: why the topic matters and how it fits the unit.")
    key_terms: list[KeyTerm]
    common_misconceptions: list[str] = Field(
        description="Frequent student errors paired with the correct understanding."
    )
    practice_questions: list[PracticeQuestion]
    summary: str = Field(description="Concise recap a student could revise from.")


class LOCoverage(BaseModel):
    code: str
    covered: bool = Field(description="True only if the notes genuinely TEACH this objective to "
                          "the required depth — not merely mention it.")
    where: str = Field(description="Section heading(s) covering it, or '' if uncovered.")
    confidence: Literal["high", "medium", "low"]
    gap_note: str = Field(default="", description="If not fully covered, what is missing.")


class CoverageReport(BaseModel):
    items: list[LOCoverage] = Field(description="One entry per learning objective in the contract.")
    review_flags: list[str] = Field(
        default_factory=list,
        description="Statements that look factually doubtful, or above/below the stated depth.",
    )


class ClassNotes(BaseModel):
    """Final assembled notes. Not a Gemini response_schema — assembled in code."""
    # context
    topic_id: str
    board: str
    subject: str
    level: str
    unit: str
    topic: str
    learning_objectives: list[LearningObjective] = Field(
        description="Echoed from the TopicSpec — the contract these notes fulfil."
    )
    # content
    overview: str
    key_terms: list[KeyTerm]
    sections: list[NoteSection]
    common_misconceptions: list[str]
    practice_questions: list[PracticeQuestion]
    summary: str
    # quality / audit
    coverage_report: list[LOCoverage] = Field(
        default_factory=list,
        description="Per-objective coverage check — the verifiable guarantee that nothing "
        "required was skipped.",
    )
    review_flags: list[str] = Field(
        default_factory=list,
        description="Low-confidence facts or coverage gaps a teacher should check before use.",
    )
    generated_at: str = Field(default="", description="ISO-8601 timestamp, stamped in code.")
