"""Pydantic schemas for the INTERACTIVE (v2) notes format.

v2 replaces the Markdown-in-<details> output with a stream of typed, interactive
BLOCKS rendered by ONE client-side dispatcher (helpers._INTERACTIVE_SHELL). Every
``Field(description=...)`` here is Gemini ``response_schema`` prompt surface.

Reuses the grounding/curated types from ``schemas`` (LearningObjective, Diagram,
LOCoverage, ExamMapCell, SpecChecklistItem, PastPapers). Blocks carry NO id — the
renderer assigns stable ids by position, so the model never invents ids.

The block ``type`` tags here MUST stay in lockstep with the JS ``renderBlock``
dispatcher in ``_INTERACTIVE_SHELL`` — ``_smoke.py`` asserts that parity.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from schemas import (  # reuse — do not redefine
    Diagram, ExamMapCell, LearningObjective, LOCoverage, PastPapers, SpecChecklistItem,
)


# ---------------------------------------------------------------------------
# Content + interactive blocks
# ---------------------------------------------------------------------------

class ProseBlock(BaseModel):
    type: Literal["prose"] = "prose"
    body: str = Field(description="A short teaching paragraph (Markdown; inline maths \\(...\\), "
        "display $$...$$). Keep to the 1-3 sentences that set up the widget(s) that follow — not "
        "a wall of text.")


class FlipCard(BaseModel):
    front: str = Field(description="Card face: the term/prompt, e.g. 'ΔfH — formation'.")
    hint: str = Field(default="", description="Small sub-label, e.g. 'flip for definition'.")
    back: str = Field(description="The definition to recite; **bold** the marking-point words.")


class FlipCardsBlock(BaseModel):
    type: Literal["flip_cards"] = "flip_cards"
    title: str = Field(default="")
    cards: list[FlipCard] = Field(description="3-6 recall cards; use for 'define/state' word-for-word recall.")


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    caption: str = Field(description="Accessible caption / aria-label.")
    headers: list[str]
    rows: list[list[str]] = Field(description="Each inner list is one row, same length as headers; "
        "cells may use **bold**.")


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    kind: Literal["tip", "mistake", "formula", "remember", "practical"] = Field(
        description="'mistake' = examiner pet-peeve / common error + its fix; 'formula' = a key "
        "rule or shortcut; 'practical' = a board Core-Practical box; 'tip'/'remember' as usual. "
        "Include only when accurate and grounded.")
    title: str = Field(default="", description="Short heading incl. a leading emoji, e.g. '⚠️ State symbols'.")
    body: str = Field(description="1-3 sentences, Markdown; grounded, no invented facts.")


class MCQOption(BaseModel):
    text: str
    correct: bool
    explanation: str = Field(description="Why this option is right/wrong, shown after it is picked. "
        "Every option needs a distinct, teaching explanation.")


class MCQBlock(BaseModel):
    type: Literal["mcq"] = "mcq"
    tag: str = Field(default="Quick check", description="Small label, e.g. 'Quick check' or 'Q1 · basic · 2 marks'.")
    question: str
    options: list[MCQOption] = Field(description="2-4 options with EXACTLY ONE correct=true.")
    targets_objective_codes: list[str] = Field(default_factory=list)


class Step(BaseModel):
    title: str = Field(description="Step lead, e.g. 'Step 1 — heat energy q.'")
    body: str = Field(description="Prose for the step (Markdown).")
    formula: str = Field(default="", description="Optional single working line, e.g. "
        "'q = 100.0 × 4.18 × 6.5 = 2717 J'. Any numbers MUST use the reference_data constants.")
    note: str = Field(default="", description="Optional trailing remark (e.g. a data-book comparison).")


class StepRevealBlock(BaseModel):
    type: Literal["step_reveal"] = "step_reveal"
    tag: str = Field(default="Worked example")
    prompt: str = Field(description="The worked problem statement (Markdown).")
    think_hint: str = Field(default="", description="A 'before you reveal' prompting question.")
    steps: list[Step] = Field(description="Ordered steps, revealed one at a time.")


class DiagnosticWrong(BaseModel):
    value: float = Field(description="A wrong answer a student is likely to produce (sign flip, wrong mass, ...).")
    tolerance: float = Field(default=0.5)
    message: str = Field(description="Targeted diagnostic feedback for THIS specific mistake.")


class MarkStep(BaseModel):
    label: str = Field(description="Scoring-point id, e.g. 'M1' / 'A1' (or AP points); should sum to `marks`.")
    text: str


class NumericBlock(BaseModel):
    type: Literal["numeric"] = "numeric"
    label: str = Field(description="e.g. 'Q2 · standard · 4 marks · calculator allowed'.")
    difficulty: Literal["basic", "standard", "stretch"] = "standard"
    marks: int | None = Field(default=None, description="Board convention (marks / AP points); mark_scheme sums to this.")
    question: str
    answer: float = Field(description="The correct numeric value (sign included).")
    tolerance: float = Field(description="Absolute tolerance for a correct match (must be > 0).")
    unit: str = Field(default="")
    wrong_answers: list[DiagnosticWrong] = Field(default_factory=list,
        description="Anticipated wrong values, each with targeted feedback. None may fall within "
        "`tolerance` of `answer`.")
    mark_scheme: list[MarkStep] = Field(default_factory=list)
    sanity_check: str = Field(default="")
    targets_objective_codes: list[str] = Field(default_factory=list)


class SimInput(BaseModel):
    key: str = Field(description="Variable name used in `expression`, e.g. 'm' (letters/digits/_ only).")
    label: str
    unit: str = Field(default="")
    min: float
    max: float
    step: float
    default: float


class SimConstant(BaseModel):
    key: str = Field(description="Name used in `expression`, e.g. 'c'.")
    value: float = Field(description="MUST equal the reference_data value for this quantity (e.g. c = 4.18).")
    label: str = Field(default="")


class SimToggle(BaseModel):
    label: str
    factor: float = Field(description="Multiplier applied to the result when checked, e.g. 0.85 for heat loss.")
    note: str = Field(default="")


class SimBlock(BaseModel):
    type: Literal["sim"] = "sim"
    title: str
    inputs: list[SimInput]
    constants: list[SimConstant] = Field(default_factory=list)
    expression: str = Field(description="A SAFE arithmetic expression over the input/constant KEYS "
        "only (allowed tokens: those keys, numbers, + - * / and parentheses — NO functions, no other "
        "names). e.g. '-(m*c*t)/1000/n'. The renderer evaluates it with a tokenized parser, never eval().")
    qline_template: str = Field(default="", description="Human working line with {key} tokens for "
        "inputs/constants, plus {q} for the intermediate below and {result} for the final output, "
        "e.g. 'q = {m} × {c} × {t} = {q} J'.")
    qline_expression: str = Field(default="", description="Optional SAFE arithmetic expression (same "
        "rules as `expression`) for the intermediate {q} shown in qline_template, e.g. 'm*c*t'.")
    output_label: str = Field(default="Result")
    output_unit: str = Field(default="")
    output_format: Literal["signed_1dp", "signed_0dp", "plain_2dp"] = "signed_1dp"
    toggle: SimToggle | None = None


class SortBucket(BaseModel):
    key: str
    label: str
    accent: Literal["exo", "endo", "neutral"] = "neutral"


class SortItem(BaseModel):
    label: str = Field(description="Chip text, e.g. 'H–H · 436'.")
    value: float | None = Field(default=None, description="Numeric magnitude for the result; from reference_data.")
    correct_bucket: str = Field(description="Which bucket.key this item belongs in.")


class SortBlock(BaseModel):
    type: Literal["sort"] = "sort"
    title: str = Field(default="")
    prompt: str = Field(description="The reaction/expression to sort against, e.g. 'H–H + Cl–Cl → 2 H–Cl'.")
    buckets: list[SortBucket] = Field(description="e.g. broken / made.")
    items: list[SortItem]
    result_expression: str = Field(default="", description="How buckets combine, e.g. 'sum(broken) - sum(made)'.")
    success_note: str = Field(default="")
    failure_hint: str = Field(default="")


class ToggleState(BaseModel):
    key: str = Field(description="Stable id, e.g. 'exo' / 'endo'.")
    label: str = Field(description="Toggle button text, e.g. 'Exothermic'.")
    caption: str = Field(description="Caption shown when this state is active.")
    product_position: Literal["above", "below"] = Field(description="Where the product level sits vs "
        "reactants on the energy axis. The renderer draws the curve — you only choose.")
    dh_sign: Literal["+", "−"] = "−"
    dh_label: str = Field(default="ΔH")
    accent: Literal["exo", "endo"] = "exo"


class ToggleDiagramBlock(BaseModel):
    type: Literal["toggle_diagram"] = "toggle_diagram"
    title: str = Field(default="")
    template: Literal["energy_profile"] = Field(description="Which renderer-owned SVG to draw. Only "
        "'energy_profile' exists; the renderer owns the geometry so it can never be mis-drawn — you "
        "supply labelled states only.")
    states: list[ToggleState] = Field(description="2+ mutually exclusive labelled states.")


class CycleEdge(BaseModel):
    frm: Literal["top_left", "top_right", "bottom"]
    to: Literal["top_left", "top_right", "bottom"]
    label: str = Field(description="Arrow label, e.g. 'ΣΔfH (reactants)'.")
    accent: Literal["exo", "endo", "neutral"] = "neutral"


class CycleDiagramBlock(BaseModel):
    type: Literal["cycle_diagram"] = "cycle_diagram"
    caption: str = Field(description="Explains the route and the resulting subtraction direction.")
    top_left: str = Field(default="Reactants")
    top_right: str = Field(default="Products")
    bottom: str = Field(description="Bottom node label, e.g. 'Elements (standard states)'.")
    edges: list[CycleEdge] = Field(description="The 3 arrows of the Hess triangle, with directions + labels.")
    route_note: str = Field(default="")


class RevealBlock(BaseModel):
    """The hero 'hook' — a curiosity question with a hidden answer."""
    type: Literal["reveal"] = "reveal"
    emoji: str = Field(default="🔥")
    question: str = Field(description="The hooky question, e.g. 'Why do hand warmers get hot?'")
    teaser: str = Field(description="Visible curiosity-gap text before the reveal.")
    answer: str = Field(description="The revealed answer incl. the key equation/value.")


class AccordionItem(BaseModel):
    summary: str
    detail: str


class AccordionBlock(BaseModel):
    type: Literal["accordion"] = "accordion"
    items: list[AccordionItem] = Field(description="Collapsible items, e.g. common mistakes.")


class FigureBlock(BaseModel):
    """A static visual — reuses the v1 Diagram and the existing image-fetch pipeline."""
    type: Literal["figure"] = "figure"
    diagram: Diagram


# Registered block models — the ONE source of truth for both the union and the
# type-tag list that _smoke.py checks against the JS dispatcher.
_BLOCK_MODELS = (
    ProseBlock, CalloutBlock, TableBlock, FlipCardsBlock, MCQBlock, StepRevealBlock,
    NumericBlock, SimBlock, SortBlock, ToggleDiagramBlock, CycleDiagramBlock,
    RevealBlock, AccordionBlock, FigureBlock,
)
# Plain Union (NOT a discriminated union): Pydantic emits `anyOf` with no
# `discriminator`/`oneOf` keyword, which is what the Vertex response-schema
# transformer accepts. Output still resolves by each block's distinct `type`
# Literal via Pydantic smart-union matching.
Block = Union[_BLOCK_MODELS]

BLOCK_TYPES = [m.model_fields["type"].default for m in _BLOCK_MODELS]
# Blocks that count toward the progress tracker / section ticks.
INTERACTIVE_BLOCK_TYPES = [
    "flip_cards", "mcq", "step_reveal", "numeric", "sim", "sort", "toggle_diagram", "reveal",
]
# SVG templates the renderer knows how to draw for toggle_diagram.
SVG_TEMPLATES = ["energy_profile"]


# ---------------------------------------------------------------------------
# Section + assembled document
# ---------------------------------------------------------------------------

class InteractiveSection(BaseModel):
    heading: str
    spec_label: str = Field(default="", description="Student-facing pill, e.g. 'Spec 8.5–8.6 · CP8 skills'.")
    covers_objective_codes: list[str] = Field(default_factory=list)
    blocks: list[Block] = Field(description="Ordered content + interactive blocks for this section.")


class Hero(BaseModel):
    eyebrow: str = Field(description="Small label, e.g. 'Edexcel A-Level · Chemistry (9CH0) · Topic 8'.")
    title: str
    lede: str = Field(description="One-paragraph hooky intro to the whole topic.")
    icon: str = Field(default="", description="One emoji for the topbar/hero.")


class CommandWord(BaseModel):
    word: str = Field(description="The command verb, e.g. 'Define'.")
    gloss: str = Field(description="What it demands in THIS topic, and where the marks are.")


class Finish(BaseModel):
    heading: str = Field(default="Topic progress")
    next_topic: str = Field(default="")


class SpecChecklist(BaseModel):
    source_title: str = Field(default="")
    source_citation: str = Field(default="")
    items: list[SpecChecklistItem] = Field(default_factory=list)


class ExamMap(BaseModel):
    title: str = Field(default="Where this topic appears in your exams")
    cells: list[ExamMapCell] = Field(default_factory=list)


class InteractiveNotes(BaseModel):
    """Final assembled interactive notes. Built in code (like ClassNotes) — NOT one response_schema."""
    schema_version: Literal["2"] = "2"
    # context
    topic_id: str
    board: str
    subject: str
    level: str
    unit: str
    topic: str
    learning_objectives: list[LearningObjective] = Field(default_factory=list)
    # content
    hero: Hero
    exam_map: ExamMap | None = None                  # curated passthrough
    hook: RevealBlock | None = None
    sections: list[InteractiveSection] = Field(default_factory=list)
    practice: list[Block] = Field(default_factory=list, description="The practice ladder (mcq | numeric).")
    command_words: list[CommandWord] = Field(default_factory=list)
    mistakes: list[AccordionItem] = Field(default_factory=list)
    spec_checklist: SpecChecklist | None = None      # curated items + generated recap
    past_papers: PastPapers | None = None            # curated passthrough
    finish: Finish = Field(default_factory=Finish)
    # audit
    coverage_report: list[LOCoverage] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    generated_at: str = ""
