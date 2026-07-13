"""Configuration for the Class Notes generator.

Mirrors the Grader's config philosophy: thin, declarative, and the place you
extend to change global behaviour. The ``curriculum/*.json`` files are
self-describing (each carries its own board/subject/level), so adding a topic is
"drop a JSON" — discovered automatically. You only touch this file to change
models, concurrency, or the house style applied across every board.
"""
from __future__ import annotations

CONFIG = {
    # Models — same split as the Grader: a heavy model where reasoning/quality
    # matters most (drafting), a fast model for planning and checking.
    "model_write":  "gemini-3.1-pro-preview",   # section drafting + finalize
    "model_plan":   "gemini-3.5-flash",         # outline
    # The v2 coverage GATE hard-blocks output, so it runs at writer strength — a
    # weak auditor rubber-stamps, which is the exact failure this gate guards
    # against. Swap to a lighter/different model here to trade rigour for a more
    # independent second opinion.
    "model_coverage": "gemini-3.1-pro-preview",
    # Temperatures — a little warmth for readable prose, deterministic checking.
    "temperature_write":  0.3,
    "temperature_plan":   0.2,
    "temperature_verify": 0.0,
    "temperature_coverage": 0.0,
    # IO
    "curriculum_dir": "curriculum",
    "out_dir": "out",
    # Concurrency for per-section drafting (mirrors grade_questions_parallel).
    "max_parallel_sections": 4,
    # v2 coverage gate: targeted section re-draws before a topic hard-fails.
    "max_coverage_retries": 2,
    # Also require deterministic structural evidence per command word (prove ->
    # step_reveal, calculate -> numeric/sim). The define/state -> flip_cards tier is
    # higher-false-positive, so it is opt-in (default off).
    "structural_gate_recall": False,
    # v2 past-paper stage: fetch real paper PDFs and cite questions verified against
    # them (two-pass). resources[] signposting is filled for every board; verified[]
    # only where a lawful paper PDF is fetchable (see sources.py).
    "generate_past_papers": True,
    "model_paper_verify": "gemini-3.1-pro-preview",  # 2nd-pass citation verifier (writer strength)
    "max_papers_per_topic": 3,
    "max_pdf_bytes": 15_000_000,                     # inline-Part fetch cap (~15 MB)
    # spec/CED grounding CLI (ground_specs.py): verify hand-seeded codes vs the official PDF.
    "model_spec_ground": "gemini-3.1-pro-preview",
    "spec_autocorrect_min_confidence": "high",       # only auto-apply corrections at >= this confidence
    "ced_slice_page_threshold": 40,                  # PDFs longer than this get pymupdf-sliced to topic pages
    # Images — Wikimedia Commons (primary) + Openverse (fallback), embedded as base64.
    "image_search": True,
    "image_vision_select": True,     # let Gemini vision pick the best/appropriate candidate
    "max_images_per_topic": 6,
    "image_width": 640,              # px; keeps each embedded image modest
    "model_vision": "gemini-3.5-flash",
}

# Injected verbatim into every generation prompt. The single place to set the
# pedagogical voice and non-negotiables across all boards and subjects.
HOUSE_STYLE = r"""You are writing class notes for a tutoring company (AP Guru) whose students sit
high-stakes exams. Non-negotiables:
- Ground every claim in the supplied learning objectives and depth profile. Do
  NOT add material beyond the stated depth, and do NOT omit a stated objective.
- Calibrate rigour to the board+level exactly — neither dumb down nor over-reach.
- Be precise with terminology; where the board rewards specific wording, use it.
- Prefer clear structure (short paragraphs, lists, bold key terms) over walls of prose.
- Write mathematics in LaTeX: inline as \(...\), display as $$...$$. Write currency
  with a plain dollar sign (e.g. $80) — never inside maths.
- Use real line breaks in Markdown; never write the literal characters backslash-n.
- Present tables as Markdown tables, never as LaTeX (no tabular, array, or hline).
- These notes are read by students: never write internal objective codes
  (e.g. SAT-ALG-LIN2-1 or ENE-1.J) in the notes themselves.
- Be careful and accurate: every claim, worked step, and callout must be grounded
  in the objectives and depth profile. When in doubt, omit it rather than guess.
- Never invent facts to fill a gap. If unsure, lower your confidence and flag it.
- Teach toward how the topic is assessed, not just the content.
"""

# Curated, board-level exam-format facts (board-general — true across subjects).
# NOT rendered as a box. Injected into write_section as grounding so each
# section's own `exam_tips` stay consistent with how the exam actually works; the
# rendered "🎯 Exam strategy" box is per-section, built from NoteSection.exam_tips.
# Hand-authored so exam-format facts stay accurate; validate against each board's
# official exam spec before relying on specifics.
BOARD_EXAM_TIPS: dict[str, list[str]] = {
    "AP": [
        "The exam has two parts — multiple-choice (Section I) and free-response (Section II). "
        "There is no penalty for a wrong multiple-choice answer, so never leave one blank.",
        "Free-response is point-scored against a published rubric: show every step and your "
        "reasoning. A bare final answer usually earns little, and follow-through credit means "
        "one early slip need not cost the later points.",
        "Scores run 1–5, so you do not need everything right — pace the multiple-choice and bank "
        "the marks you are sure of before the harder items.",
        "Answer the exact task verb: Justify, Explain, Calculate and Describe each demand a "
        "different response — do not just restate the content.",
        "Check your subject's calculator and formula/reference-sheet policy in advance; it varies "
        "by AP subject.",
    ],
    "IGCSE": [
        "Papers are tiered — Core and Extended (Supplement). Extended is required to access the "
        "highest grades, so make sure you are working to the tier you are entered for.",
        "Command words set the depth expected: 'state/give' = brief recall, 'describe' = say what "
        "happens, 'explain' = give reasons or a mechanism, 'suggest' = apply to an unfamiliar case.",
        "The mark in brackets tells you how many creditable points to make — a [3] answer needs "
        "three distinct points.",
        "Give units, balanced equations and precise terms; examiners penalise missing units, "
        "unbalanced equations and vague wording.",
        "For the practical paper, practise reading apparatus, recording results, and describing "
        "the controls and variables.",
    ],
    "SAT": [
        "The digital SAT is section-adaptive by module: your first module sets the difficulty and "
        "score ceiling of the second, so treat every question as counting.",
        "There is no penalty for a wrong answer — never leave anything blank; enter your best guess.",
        "Work in the Bluebook app: use the countdown timer, the 'mark for review' flag, and the "
        "annotation tool to pace yourself and come back to the hard questions.",
    ],
    "A-Level": [
        "Answers are marked against Assessment Objectives (AO1 recall, AO2 application, AO3 "
        "analysis/evaluation) — 'explain' and 'evaluate' want reasoning, not just facts.",
        "Show full working: method marks are awarded for correct steps even if the final answer is "
        "wrong. Always state units and appropriate significant figures.",
        "Papers are synoptic — questions can combine several topics, so revise the links between "
        "topics, not just each in isolation.",
        "Longer questions also assess the quality and logical structure of your written answer — "
        "plan a coherent argument before writing.",
        "Where the spec demands exact wording (a definition, or 'prove from first principles'), "
        "learn and reproduce it precisely.",
    ],
    "AMC 10": [
        "The AMC 10 is 25 multiple-choice questions in 75 minutes with no calculator, and the problems "
        "get harder as you go — secure the earlier questions before you sink time into the last five.",
        "Scoring is unusual: 6 points for a correct answer, 1.5 for a blank, and 0 for a wrong one. A "
        "random guess is worth LESS than leaving it blank, so only guess once you can rule out choices.",
        "Every answer is one of five choices (A-E) — use them: plugging in the options, estimating, or "
        "checking parity and size can be faster than a full solution.",
        "It rewards insight over grinding: look for symmetry, a clever substitution, complementary "
        "counting, or a well-placed auxiliary line before committing to heavy algebra.",
        "A high score (top 2.5%, or the announced cutoff) qualifies you for the AIME, so accuracy on the "
        "problems you do reach beats rushing to attempt all 25.",
    ],
}


# Subject-specific exam-format facts, layered ON TOP of the board-general
# BOARD_EXAM_TIPS for the level. Keyed (level, subject) so a level whose facts
# genuinely differ by subject — e.g. SAT Math (Desmos calculator, grid-ins) vs SAT
# Reading & Writing (one passage per question, decide before you read the choices) —
# no longer has to share one list. An absent (level, subject) => just the general tips.
BOARD_SUBJECT_EXAM_TIPS: dict[tuple[str, str], list[str]] = {
    ("SAT", "Mathematics"): [
        "A built-in Desmos graphing calculator is available throughout the Math section — set up the "
        "algebra first, then use it to check or to solve graphically.",
        "Pace is roughly a minute and a half per question, and the wrong options are engineered around "
        "common slips like sign errors and swapped slope and intercept — watch for the trap.",
        "About a quarter of Math questions are student-produced 'grid-in' responses with no choices: "
        "follow the entry rules for fractions, decimals and negatives, and stay within the character limit.",
    ],
    ("SAT", "Reading and Writing"): [
        "Each question is a single short passage with one question — you have a little over a minute "
        "each, so read for the point rather than rereading every word.",
        "For grammar and transitions questions, decide what the sentence needs BEFORE reading the "
        "choices: the wrong options are all grammatically clean and differ only in logic or punctuation.",
        "Answer only from what the passage states — never from outside knowledge about the topic.",
    ],
}


def exam_tips_for(level: str, subject: str) -> list[str]:
    """Board-general exam-format tips for the level, plus any subject-specific tips.
    Keyed so, e.g., SAT Reading & Writing does not inherit SAT Math-only facts
    (Desmos, grid-ins). An absent subject => just the general tips for the level."""
    return BOARD_EXAM_TIPS.get(level, []) + BOARD_SUBJECT_EXAM_TIPS.get((level, subject), [])
