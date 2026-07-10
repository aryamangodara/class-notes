# Class Notes Generator

Feed a **topic** for an exam board (AP / IGCSE / SAT / A-Level) and generate
**curriculum-grounded class notes** — stored as **JSON** (the source of truth) and
rendered by a self-contained, interactive **HTML page that renders the JSON embedded
in it** (collapsible sections, LaTeX, diagrams, embedded images).

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
         (cover      (grounded,        (Wikimedia/     (key terms, (every     (json
          every       parallel,         Openverse,      callouts,   objective   + html)
          objective)  outline-aware)    vision-picked)  practice…)  taught?)
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
- **Per-section exam strategy** — each section ends with a 🎯 box of exam pointers
  specific to that subtopic (command words, mark-scheme quirks, common errors),
  grounded in the board's curated exam format.
- **Practice ladder** — 5–6 questions from *basic → stretch*, each with
  board-appropriate marks (points for AP) and a mark-scheme solution; every section
  shows its spec-point codes.
- **Coverage footer + review flags** — an audit trail for the teacher.

## Interactive format (v2)

`py -3 notes.py --v2 <topic_id>` generates a richer, **single-scroll interactive
lesson** (`out/<id>.interactive.html`) — a sticky progress tracker, flip-card
definitions, multiple-choice checks with per-option feedback, step-by-step reveal
of worked examples, **live calculators** (sims), drag-to-bucket sorts, parameterized
energy-profile / cycle diagrams, a **numeric practice ladder** with mark schemes and
diagnostic wrong-answer feedback, a tickable **spec checklist**, and a curated
exam-map + past-paper panel. It's a block-based format (`schemas_v2.py`) rendered by
one data-driven client-side dispatcher (`render_v2.py`); the renderer **owns all
diagram geometry and calculator arithmetic** (no `eval`, no model-authored markup),
so it's safe by construction. Both formats are generated from the same grounded
`curriculum/*.json`.

## Setup

```bash
pip install -r requirements.txt         # google-genai, pydantic, pillow, python-dotenv, pymupdf
cp .env.example .env                     # add GEMINI_API_KEY (the Grader's key works; Vertex also supported)
```

## Run

Use **`py -3`** on this machine (bare `python` lacks the deps — see CLAUDE.md):

```bash
py -3 notes.py --list                                # the 10 seeded topics
py -3 notes.py ap-chem-atomic-structure-periodicity  # one topic, classic format (live Gemini)
py -3 notes.py --v2 alevel-chem-enthalpy-changes     # one topic, INTERACTIVE format
py -3 notes.py --all                                 # every topic (add --v2 for interactive)
py -3 _smoke.py                                       # offline v1 render self-test (no API key)
py -3 _smoke_v2.py                                    # offline v2 self-test (parity, safety, invariants)
```

Outputs land in `out/<topic_id>.{json,html}` — the `.json` is the source of truth
and the `.html` **embeds that JSON and renders it in the browser**, so you can just
**double-click the `.html`** (no server needed) for the collapsible layout with
rendered maths, diagrams and images.

> **Re-render without regenerating** (apply render/CSS changes to existing notes, no API cost):
> ```bash
> py -3 -c "from pathlib import Path; from schemas import ClassNotes; from helpers import save_notes; [save_notes(ClassNotes.model_validate_json(Path(p).read_text(encoding='utf-8'))) for p in sorted(Path('out').glob('*.json'))]"
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
  `config.py`.

## Layout

```
config.py      models/temperatures, image settings, HOUSE_STYLE, BOARD_EXAM_TIPS
schemas.py     Pydantic — TopicSpec (grounding) + ClassNotes/Callout/Diagram (v1 output)
schemas_v2.py  Pydantic — the v2 block vocabulary + InteractiveNotes
helpers.py     gemini client, v1 pipeline stages, image search + vision, v1 renderers
pipeline_v2.py v2 pipeline stages (generate_interactive_notes)
render_v2.py   the v2 interactive renderer + validate_interactives
prompts/       outline / write_section / finalize / verify + v2_*  (plain text, edit freely)
curriculum/    one TopicSpec JSON per topic — the grounding store (10 topics)
notes.py       CLI: feed a topic (--v2 for the interactive format)
_smoke.py / _smoke_v2.py   offline self-tests (no API key)
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
