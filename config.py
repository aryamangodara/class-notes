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
    "model_verify": "gemini-3.5-flash",         # coverage audit
    # Temperatures — a little warmth for readable prose, deterministic checking.
    "temperature_write":  0.3,
    "temperature_plan":   0.2,
    "temperature_verify": 0.0,
    # IO
    "curriculum_dir": "curriculum",
    "out_dir": "out",
    # Concurrency for per-section drafting (mirrors grade_questions_parallel).
    "max_parallel_sections": 4,
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
