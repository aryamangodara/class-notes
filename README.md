# Class Notes Generator

Feed a **topic** for an exam board (AP / IGCSE / SAT / A-Level) and generate
**curriculum-grounded class notes** — rendered to Markdown, a self-contained,
interactive HTML page (collapsible sections, LaTeX, diagrams, embedded images),
and JSON.

Sister project to `../Grader`: same Gemini stack, same `.env`, same
`config / schemas / helpers / prompts` split. Where the Grader reads *how a topic
is assessed* to score answers, this reads the *same curriculum* to teach toward it.

## Why this isn't just "ask an AI to write notes"

A generic prompt blends every board together and drifts. The value is
**grounding**: each topic carries the *exact* syllabus learning objectives, a
**depth profile** (how far this board takes the idea), and **assessment notes**
(how it's examined). Generation is checked against that contract, and a curated
per-board **exam strategy** is layered on top.

## Pipeline

```
topic ─► outline ─► draft sections ─► fetch images ─► finalize ─► verify ─► render
         (cover      (grounded,        (Wikimedia/     (key terms, (every     (md +
          every       parallel,         Openverse,      callouts,   objective  html +
          objective)  outline-aware)    vision-picked)  practice…)  taught?)   json)
```

- **Grounded & careful** — every section, callout and image is tied to the
  objectives; the verifier flags anything doubtful or beyond the depth profile
  (surfaced, not hidden — the Grader philosophy).
- **Verifiable coverage** — a per-objective audit: *is each learning objective
  actually taught?*

## What's in a generated note

- **Collapsible document** — every section folds (`<details>`), collapsed by
  default; only the title + overview stay as visible front matter.
- **Inline callouts** — 💡 Quick Tip · ⚠️ Common Mistake · 📐 Key Formula/Fact ·
  🧠 Remember — colour-coded boxes placed in context.
- **Real images** — labelled diagrams pulled from **Wikimedia Commons / Openverse**
  (CC / public-domain only), the best candidate chosen by Gemini vision, embedded
  base64 (self-contained) with a caption + licence attribution.
- **Diagrams & maths** — Mermaid flowcharts and LaTeX (MathJax).
- **Exam strategy box** — a curated per-board 🎯 box (AP / IGCSE / SAT / A-Level
  exam format, command words, mark-scheme quirks), with the topic's own exam tips
  folded in.
- **Coverage footer + review flags** — an audit trail for the teacher.

## Setup

```bash
pip install -r requirements.txt         # google-genai, pydantic, pillow, python-dotenv, pymupdf
cp .env.example .env                     # add GEMINI_API_KEY (the Grader's key works; Vertex also supported)
```

## Run

Use **`py -3`** on this machine (bare `python` lacks the deps — see CLAUDE.md):

```bash
py -3 notes.py --list                                # the 7 seeded topics
py -3 notes.py ap-chem-atomic-structure-periodicity  # one topic (live Gemini calls)
py -3 notes.py --all                                 # every topic
py -3 _smoke.py                                       # offline render self-test (no API key, no network)
```

Outputs land in `out/<topic_id>.{md,html,json}`. **Open the `.html`** in a
browser for the collapsible layout with rendered maths, diagrams and images.

> **Re-render without regenerating** (apply render/CSS changes to existing notes, no API cost):
> ```bash
> py -3 -c "from pathlib import Path; from schemas import ClassNotes; from helpers import save_notes; [save_notes(ClassNotes.model_validate_json(Path(p).read_text(encoding='utf-8'))) for p in sorted(Path('out').glob('*.json'))]"
> ```

## Seeded topics (7 — three subjects across four boards)

| topic_id | board | subject |
|---|---|---|
| `ap-bio-cellular-respiration` | AP | Biology |
| `ap-chem-atomic-structure-periodicity` | AP | Chemistry |
| `igcse-bio-photosynthesis` | Cambridge IGCSE | Biology |
| `igcse-chem-electrolysis` | Cambridge IGCSE | Chemistry |
| `alevel-maths-differentiation-first-principles` | Edexcel A-Level | Mathematics |
| `alevel-chem-enthalpy-changes` | Edexcel A-Level | Chemistry |
| `sat-math-linear-equations` | SAT | Mathematics |

Specs are hand-seeded and tagged "validate against the official spec"; depth
profiles deliberately differ per board (e.g. SAT vs A-Level maths rigour) — that
calibration is the point of grounding.

## Add a topic or board

- **Topic:** drop a `curriculum/<topic_id>.json` matching the `TopicSpec` schema
  (`schemas.py`) — auto-discovered, no code change. For production, extract these
  from official CED / syllabus PDFs via `prompts/spec_extract.txt`.
- **Exam strategy for a new board:** add a `BOARD_EXAM_TIPS[level]` entry in
  `config.py`.

## Layout

```
config.py      models/temperatures, image settings, HOUSE_STYLE, BOARD_EXAM_TIPS
schemas.py     Pydantic — TopicSpec (grounding) + ClassNotes/Callout/Diagram (output); also Gemini response_schema
helpers.py     gemini client (mirrored), pipeline stages, image search + vision, renderers
prompts/       outline / write_section / finalize / verify / spec_extract  (plain text, edit freely)
curriculum/    one TopicSpec JSON per topic — the grounding store (7 topics)
notes.py       CLI: feed a topic
_smoke.py      offline render self-test (no API key)
out/           generated notes (gitignored)
```

See **[CLAUDE.md](CLAUDE.md)** for architecture details and the conventions
specific to this repo (the `\(...\)` maths convention, the render sanitizers, the
image/licence policy, the `py -3` interpreter, etc.).

## Roadmap

- **Curriculum from PDFs** — wire `spec_extract.txt` to parse official specs into
  `curriculum/*.json` (validate the hand-seeded specs).
- **PDF / DOCX export** — printable, teacher-editable notes.
- **Teacher-in-the-loop** — a review checkpoint on flagged content before publish.
- **Custom diagrams** — commission/generate the bespoke illustrations free
  libraries lack (labelled apparatus, coordinate graphs).
- **S3 distribution** — reuse the Grader's `upload_to_s3.py`.
- **Claude option** — the pipeline is provider-agnostic; A/B against Gemini.
