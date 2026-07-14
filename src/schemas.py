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
  * Shared output parts — ``Diagram``, ``ExamMapCell``, ``PastPapers``,
    ``SpecChecklistItem``, ``LOCoverage`` / ``CoverageReport``, ``ImageChoice``,
    ``OutlineSection`` / ``NotesOutline``: reused by the interactive v2 schemas in
    ``schemas_v2.py`` (the assembled output type lives there, not here).
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


# ---------------------------------------------------------------------------
# Curated exam-format layer (hand-authored per topic; NEVER model-generated).
# Feeds the interactive-notes exam map, spec checklist and past-paper panels
# verbatim — validate every value against the official specification.
# ---------------------------------------------------------------------------

class ExamMapCell(BaseModel):
    key: str = Field(description="Short label, e.g. 'Papers' or 'Core Practical'.")
    value: str = Field(description="The fact. May contain light HTML (e.g. <b>, <a>).")


class VerifiedPaper(BaseModel):
    label: str = Field(description="Reference as verified against the paper PDF, e.g. "
                       "'June 2024 · Paper 1 · Q5 · 10 marks'.")
    summary: str = Field(description="What the question tests, in our own words (plain text).")
    url: str = Field(default="", description="Source paper URL — set by the pipeline from the curated "
                     "source registry, never model-authored.")


class PastPapers(BaseModel):
    intro: str = Field(default="")
    resources: list[ExamMapCell] = Field(default_factory=list, description="Where to get papers / mark schemes.")
    verified: list[VerifiedPaper] = Field(
        default_factory=list,
        description="Past-paper citations, each generated from a fetched paper PDF and independently "
        "verified against that SAME PDF in a second pass; the url is set by the pipeline from the "
        "curated source registry (never by the model), and an entry with no PDF fetched this run is "
        "disallowed. May be empty (resources-only) when no lawful paper PDF is available.",
    )
    disclaimer: str = Field(default="")


# --- past-paper GENERATION schemas (PDF-grounded; used by past_papers.py) -------
# Candidates are proposed from a fetched paper PDF, then independently verified
# against that SAME PDF. Only confirmed ones become VerifiedPaper entries, and the
# url is always set from the source registry — never by the model.

class PaperCitationCandidate(BaseModel):
    paper_label: str = Field(description="The paper as printed on the PDF, e.g. 'June 2024 · Paper 1'.")
    question: str = Field(description="Question reference as printed, e.g. 'Q5' or 'Q6(d)'.")
    marks: int | None = Field(default=None, description="Total marks for that question, or null if unclear.")
    summary: str = Field(description="What the question tests, in OUR OWN WORDS (plain text, NO HTML/markup); "
                         "tie it to this topic's objectives.")
    evidence_quote: str = Field(description="A SHORT verbatim snippet copied from the PDF that anchors this "
                                "citation (proves the question is really in this paper).")
    topic_relevance: str = Field(description="Why this question tests THIS topic (one sentence).")


class CandidateCitations(BaseModel):
    items: list[PaperCitationCandidate] = Field(
        default_factory=list,
        description="Questions in THIS paper PDF that assess this topic. Empty if none — never invent one.")


class CitationVerdict(BaseModel):
    paper_label: str = Field(description="Echo the candidate's paper_label.")
    question: str = Field(description="Echo the candidate's question reference.")
    confirmed: bool = Field(description="True ONLY if you can locate this exact question in the attached PDF AND "
                            "it genuinely assesses this topic. If unsure, false.")
    marks_ok: bool = Field(default=False, description="True if the candidate's marks match the PDF.")
    corrected_marks: int | None = Field(default=None, description="The correct mark total from the PDF, if different.")
    corrected_question: str = Field(default="", description="Corrected question reference if the candidate's was wrong.")
    verified_summary: str = Field(default="", description="A PDF-grounded own-words summary (plain text, NO HTML) you "
                                  "endorse; used verbatim when confirmed.")
    reason: str = Field(default="", description="Why confirmed / rejected.")


class VerificationReport(BaseModel):
    items: list[CitationVerdict] = Field(
        default_factory=list, description="One verdict per candidate, in the same order as the candidates.")


# --- spec/CED grounding (PDF-verified; used by ground_specs.py) -----------------
# Each hand-seeded code + statement is checked against the official spec PDF. Only
# high-confidence corrections are auto-applied to curriculum/*.json; 'absent' is
# report-only (a mis-sliced PDF must never silently delete an objective).

class SpecItemVerdict(BaseModel):
    kind: Literal["objective", "checklist"] = Field(description="Which list the item came from.")
    given_code: str = Field(description="The code as currently in our curriculum JSON.")
    given_text: str = Field(default="", description="The statement / can-do text as currently in our JSON.")
    status: Literal["confirmed", "corrected", "absent"] = Field(
        description="'confirmed' = matches the spec PDF; 'corrected' = present but the code or text is "
        "wrong (give the fix); 'absent' = you could NOT find this in the attached spec PDF.")
    corrected_code: str = Field(default="", description="The correct code from the PDF, if different.")
    corrected_text: str = Field(default="", description="The correct statement/can-do from the PDF, if different.")
    confidence: Literal["high", "medium", "low"] = Field(
        default="low", description="Confidence in this verdict. Only 'high' corrections are auto-applied.")
    evidence: str = Field(default="", description="A SHORT verbatim snippet from the spec PDF supporting the verdict.")


class SpecGroundingReport(BaseModel):
    items: list[SpecItemVerdict] = Field(default_factory=list, description="One verdict per item given, same order.")
    missing_from_spec_note: str = Field(default="", description="Spec points found in the PDF but NOT in our "
                                        "curriculum (report-only — additions are a human decision).")
    summary: str = Field(default="", description="One-line summary of the grounding outcome.")


# --- spec EXTRACTION (PDF-grounded curriculum building; used by extract_specs.py) -----
# A full subject spec/CED covers MANY topics. We first ENUMERATE its topics (unit +
# topic + slicing keywords), then extract ONE grounded TopicSpec per topic from its own
# pages. Extraction fills the objective/depth/assessment half of a TopicSpec only; the
# curated exam-format layer is left for humans, and every extracted spec is stamped
# UNVERIFIED so it flows through ground_specs.py + human review before it can ship.

class SpecTopicEntry(BaseModel):
    unit: str = Field(description="The unit/theme this topic sits under, as titled in the spec "
                      "(e.g. 'Topic 8: Energetics I' or 'Unit 3: Cellular Energetics').")
    topic: str = Field(description="A single teachable topic title as printed in the spec — the grain of "
                       "ONE lesson (e.g. 'Enthalpy changes'), NOT a whole unit and NOT a single objective.")
    keywords: list[str] = Field(
        default_factory=list,
        description="3-8 distinctive words/phrases from THIS topic's spec text (specific terms, quantities, "
        "processes) used to locate its pages in the PDF later. Prefer specific nouns over generic verbs.")


class SpecTopicList(BaseModel):
    items: list[SpecTopicEntry] = Field(
        default_factory=list,
        description="Every discrete teachable topic in this subject specification, in spec order. One entry "
        "per lesson-sized topic; do NOT merge a whole unit into one entry or split a single objective out.")


class SpecChecklistItem(BaseModel):
    code: str = Field(description="Official spec point, e.g. '8.11'.")
    can_do: str = Field(description="'I can ...' statement in student language, from the official spec.")
    recap: str = Field(default="", description="Short 'not sure?' recap; filled in the finalize stage.")


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
    reference_data: str = Field(
        default="",
        description="Canonical constants / data values every stage MUST use verbatim (e.g. "
        "mean bond enthalpies, standard electrode potentials, molar masses). Author per topic "
        "and validate against the official data booklet; leave empty when the topic has no "
        "fixed reference values. Injected into every stage so a worked example and a practice "
        "question can never disagree on the same quantity.",
    )
    # --- curated exam-format layer (interactive v2; passthrough, never generated) ---
    exam_map: list[ExamMapCell] = Field(
        default_factory=list,
        description="CURATED exam-map cells (papers, weighting, core practical, banked marks); "
        "passed through to the interactive notes verbatim.",
    )
    spec_checklist: list[SpecChecklistItem] = Field(
        default_factory=list,
        description="CURATED official-spec statements at spec-point granularity (finer than "
        "learning_objectives). code + can_do curated; recap filled in finalize.",
    )
    spec_source_citation: str = Field(
        default="",
        description="Provenance of the spec checklist, e.g. 'Pearson Edexcel 9CH0 spec, Issue 3 (Feb 2024)'.",
    )
    past_papers: PastPapers | None = Field(
        default=None,
        description="CURATED past-paper panel incl. human-verified references; passed through verbatim.",
    )
    next_topic: str = Field(
        default="",
        description="Curated 'next topic' hint for the finish/footer, e.g. 'Topic 9 — Kinetics I'.",
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

class Diagram(BaseModel):
    caption: str
    kind: Literal["mermaid", "latex", "image", "description"] = Field(
        description="'mermaid' for flow/cycle/process diagrams AND schematic node/arrow "
        "diagrams you can draw this way — including reaction energy profiles and reaction-"
        "coordinate diagrams; 'latex' for a SINGLE mathematical expression or equation ONLY "
        "(never a table — MathJax cannot render tabular/array/hline; put tables as Markdown "
        "tables in the section body); 'image' to fetch a real labelled diagram, photo, "
        "micrograph, or map from a free image library (set content to a precise search query) "
        "— PREFER this for standard, widely-depicted visuals. 'description' is a LAST RESORT "
        "for a bespoke illustration with no mermaid/image form: it is a teacher/illustrator "
        "stub NOT shown to students, so never put content a student needs ONLY in a "
        "'description' — teach it in the body or use mermaid/image instead."
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


