# Class Notes Generator

Feed a **topic** for an exam board (AP / IGCSE / SAT / A-Level) and generate a
**curriculum-grounded, INTERACTIVE lesson** — stored as **JSON** (the source of
truth) and rendered by a self-contained **HTML page that embeds and renders that
JSON in the browser** (progress tracking, flip cards, MCQs, live sims, mark-scheme
practice, diagrams, embedded images).

Sister project to `../Grader`: same Gemini stack, same `.env`, same
`config / schemas / helpers / prompts` split. Where the Grader reads *how a topic
is assessed* to score answers, this reads the *same curriculum* to teach toward it.

## Why this isn't just "ask an AI to write notes"

A generic prompt blends every board together and drifts. The value is
**grounding**: each topic carries the *exact* syllabus learning objectives, a
**depth profile** (how far this board takes the idea), and **assessment notes**
(how it's examined). Generation is **checked against that contract and coverage is
enforced** (a topic that doesn't teach every objective is not written), and a
curated per-board **exam strategy** is layered on top.

## Pipeline

```
topic ─► outline ─► draft section ─► ENFORCE ─► fetch images ─► practice ─► finalize ─► render
         (cover     blocks           coverage   (Wikimedia/     ladder      (hero,      (.v2.json
          every     (grounded,       (regen or   Openverse,     (numeric/   command     + interactive
          objective) parallel)       hard-fail)  vision-picked) mcq)        words…)     .html)
```

- **Grounded & careful** — every block and image is tied to the objectives; the
  verifier flags anything doubtful or beyond the depth profile (surfaced, not hidden).
- **Enforced coverage** — a per-objective audit gates output: if an objective isn't
  genuinely taught, the owning section is regenerated, and if the gap survives the
  topic **hard-fails and writes nothing** (see `coverage_gate.py`).

## What's in a generated lesson

A single-scroll **interactive** page (`out/<id>.interactive.html`):

- **Sticky progress tracker** across the interactive blocks.
- **Flip-card definitions** for word-for-word recall.
- **MCQ quick-checks** with a distinct teaching explanation on every option.
- **Step-reveal worked examples** — revealed one step at a time.
- **Live sims** — parameter sliders driving a tokenized calculator (no `eval`).
- **Drag/tap-to-bucket sorts** and **parameterized energy-profile / Hess diagrams**
  (the renderer owns the geometry — never mis-drawn).
- **Numeric practice ladder** (basic → stretch) with tolerances, diagnostic
  wrong-answer feedback, and M1/A1 mark schemes; board-appropriate marks (points for AP).
- **Curated exam map, spec checklist and past-paper panel**, plus a per-section
  🎯 exam-strategy angle.
- **Coverage report + review flags** — an audit trail for QA.

It's a **block-based** format (`schemas_v2.py`) rendered by one data-driven
client-side dispatcher (`render_v2.py`); the renderer **owns all diagram geometry and
calculator arithmetic** (no `eval`, no model-authored markup), so it's safe by
construction.

## Setup

```bash
pip install -r requirements.txt         # google-genai, pydantic, pillow, python-dotenv, pymupdf
cp .env.example .env                     # add GEMINI_API_KEY (the Grader's key works; Vertex also supported)
```

## Run

Use **`py -3`** on this machine (bare `python` lacks the deps — see CLAUDE.md):

```bash
py -3 notes.py --list                                # the 10 seeded topics
py -3 notes.py alevel-chem-enthalpy-changes          # one topic (live Gemini)
py -3 notes.py --all                                 # every topic
py -3 _smoke_v2.py                                   # offline self-test (no API key)
```

Outputs land in `out/<topic_id>.v2.json` (source of truth) + `<topic_id>.interactive.html`.
The `.html` **embeds that JSON and renders it in the browser**, so you can just
**double-click it** (no server needed).

> **Re-render without regenerating** (apply render/CSS changes to existing notes, no API cost):
> ```bash
> py -3 -c "from pathlib import Path; from schemas_v2 import InteractiveNotes; from pipeline_v2 import save_interactive_notes; [save_interactive_notes(InteractiveNotes.model_validate_json(Path(p).read_text(encoding='utf-8'))) for p in sorted(Path('out').glob('*.v2.json'))]"
> ```

## Seeded topics (10 — four subjects across four boards)

| topic_id | board | subject |
|---|---|---|
| `ap-bio-cellular-respiration` | AP | Biology |
| `ap-chem-atomic-structure-periodicity` | AP | Chemistry |
| `ap-physics-newtons-laws` | AP | Physics |
| `igcse-bio-photosynthesis` | Cambridge IGCSE | Biology |
| `igcse-chem-electrolysis` | Cambridge IGCSE | Chemistry |
| `igcse-physics-forces-motion` | Cambridge IGCSE | Physics |
| `alevel-maths-differentiation-first-principles` | Edexcel A-Level | Mathematics |
| `alevel-chem-enthalpy-changes` | Edexcel A-Level | Chemistry |
| `alevel-physics-forces-motion` | Edexcel A-Level | Physics |
| `sat-math-linear-equations` | SAT | Mathematics |

Specs are hand-seeded and tagged "validate against the official spec"; depth
profiles deliberately differ per board (e.g. SAT vs A-Level maths rigour) — that
calibration is the point of grounding.

## Add a topic or board

- **Topic:** drop a `curriculum/<topic_id>.json` matching the `TopicSpec` schema
  (`schemas.py`) — auto-discovered, no code change. For production, extract these
  from official CED / syllabus PDFs via `prompts/spec_extract.txt`.
- **Exam strategy for a new board:** add a `BOARD_EXAM_TIPS[level]` entry in
  `config.py` (and a `BOARD_SUBJECT_EXAM_TIPS[(level, subject)]` overlay when the facts
  differ by subject, e.g. SAT Math vs SAT Reading & Writing).

## Layout

```
config.py        models/temps, coverage gate, image settings, HOUSE_STYLE, BOARD_EXAM_TIPS
schemas.py       Pydantic — TopicSpec (grounding) + shared output parts (Diagram, PastPapers,
                 LOCoverage, ExamMapCell, SpecChecklistItem, ImageChoice, NotesOutline)
schemas_v2.py    Pydantic — the block vocabulary + InteractiveNotes
helpers.py       gemini client + retry, prompt loading, grounding, the outline stage, image search
coverage_gate.py deterministic, genai-free coverage-gate logic (CoverageError + helpers)
pipeline_v2.py   the pipeline (generate_interactive_notes + save_interactive_notes)
render_v2.py     the interactive renderer + validate_interactives
prompts/         outline / verify / v2_* / spec_extract  (plain text, edit freely)
curriculum/      one TopicSpec JSON per topic — the grounding store (10 topics)
notes.py         CLI entry point
_smoke_v2.py     offline self-test (no API key)
out/             generated notes (gitignored)
```

See **[CLAUDE.md](CLAUDE.md)** for architecture details and the conventions specific
to this repo (the `\(...\)` maths convention, the renderer safety doctrine, the
image/licence policy, the `py -3` interpreter, etc.).

## Roadmap

Recently shipped: enforced coverage (regenerate-or-hard-fail) + deterministic structural
checks, PDF-grounded past papers (`sources.py` + `past_papers.py`), spec-code grounding
(`ground_specs.py`), and a deterministic tutor spot-check (`spotcheck.py`).

- **PDF / DOCX export** — printable, teacher-editable notes.
- **S3 distribution** — reuse the Grader's `upload_to_s3.py`.
- **Claude option** — the pipeline is provider-agnostic; A/B against Gemini.
