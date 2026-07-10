# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

A curriculum-grounded **class-notes generator** for AP / IGCSE / SAT / A-Level.
Feed a topic id; a multi-stage Gemini pipeline (outline → draft sections → fetch
images → finalize → verify coverage) renders notes to
`out/<topic_id>.{json,html}` — the JSON is the source of truth and the `.html` is a
self-contained page that embeds that JSON and renders it in the browser (no `.md` is
persisted; opens straight from disk, no server). Sister project to `../Grader` — same
single-vendor Gemini stack and `config / schemas / helpers / prompts` split; the
notes are grounded in the same curriculum the Grader assesses against.

## Commands

Use **`py -3`**, NOT bare `python` (see Conventions):

```bash
py -3 notes.py --list           # list discovered topics
py -3 notes.py <topic_id>       # generate one topic (live Gemini calls)
py -3 notes.py --all            # generate every topic
py -3 notes.py --v2 <topic_id>  # generate the INTERACTIVE v2 format (also: --v2 --all)
py -3 _smoke.py                 # OFFLINE v1 render self-test (stubs Gemini; no key/network)
py -3 _smoke_v2.py              # OFFLINE v2 self-test (schema<->dispatcher parity, safety, invariants)
```

There is no build step and no test suite beyond `_smoke.py` — it's the fast
regression check for the renderer. **Run it after any `helpers.py` / `schemas.py`
change.**

**Re-render existing notes without regenerating** (apply render/CSS changes, no API):
```bash
py -3 -c "from pathlib import Path; from schemas import ClassNotes; from helpers import save_notes; [save_notes(ClassNotes.model_validate_json(Path(p).read_text(encoding='utf-8'))) for p in sorted(Path('out').glob('*.json'))]"
```
Everything (including embedded `image_src` base64) is stored in the JSON, so
`render_markdown` / `render_html` are pure functions of the saved `ClassNotes` —
render-layer changes apply instantly to all notes with no Gemini calls. Prefer
this over `--all` when you only touched rendering.

## Interactive v2 format (block-based; the new primary output)

A richer output format lives ALONGSIDE v1: a single-scroll INTERACTIVE lesson
(progress tracking, flip-card definitions, MCQs with per-option feedback,
step-reveal worked examples, live sims, drag-to-bucket sorts, parameterized
energy-profile / Hess SVGs, a numeric practice ladder with tolerance + diagnostic
wrong-answers + mark schemes, a spec checklist, and a curated exam-map + past-paper
panel). `notes.py --v2 <id>` writes `out/<id>.v2.json` + `<id>.interactive.html`
(never overwrites v1 artifacts). Target design contract: `enthalpy-interactive-full.html`.

- `schemas_v2.py` — the block vocabulary: an ordered list of typed `Block`s per
  `InteractiveSection` (prose / callout / table / flip_cards / mcq / step_reveal /
  numeric / sim / sort / toggle_diagram / cycle_diagram / reveal / accordion /
  figure) + the assembled `InteractiveNotes`. The union is a **plain `Union`** (→
  `anyOf`), NOT discriminated — the Vertex schema transformer rejects
  `oneOf`/`discriminator`. Blocks carry NO id; the renderer assigns ids by position.
- `render_v2.py` — ONE client-side block-dispatch renderer (`renderBlock`) + the
  **safety doctrine**: the renderer OWNS all SVG geometry (`SVG_TEMPLATES`) and sim
  arithmetic (a tokenized evaluator, never `eval`); model strings reach the DOM via
  `textContent`, never `innerHTML` (prose is the one `marked` path). Also
  `validate_interactives` (deterministic post-gen checks → `review_flags`).
- `pipeline_v2.py` — `generate_interactive_notes`: outline → section blocks
  (parallel) → images for `figure` blocks → practice ladder → finalize (hero / hook
  / command words / mistakes / checklist recaps) → verify + validate. Prompts:
  `prompts/v2_{write_section,write_practice,finalize}.txt`. **Prompt templates are
  `str.format`-ed — never put literal `{`/`}` in them (a `{-1}` unit example once
  broke every section-write with `KeyError`).**
- Curated, NEVER generated: `TopicSpec.exam_map / spec_checklist / past_papers /
  next_topic` (hand-authored per topic; the "Verified" past-paper citations must be
  human-verified — leave empty until confirmed against the real papers).
- **Parity contract** (asserted by `_smoke_v2.py`): the block `type` set in
  `schemas_v2.BLOCK_TYPES` === the `renderBlock` `case` set in the JS — keep them in
  lockstep. v2 drops Mermaid; keeps MathJax + `marked` (prose only) + a Google-Fonts link.

## Setup contract

- `.env` with ONE auth method (same as the Grader — its key works here):
  - `GEMINI_API_KEY=...` (AI Studio), or
  - `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` (Vertex AI).
  `get_gemini_client()` prefers the key and falls back to Vertex.
- `curriculum/*.json` — one `TopicSpec` per topic, self-describing (carries its
  own board/subject/level); discovered automatically by `discover_topics()`.

## Architecture & data flow

```
config.py    CONFIG (models, temps, image settings), HOUSE_STYLE, BOARD_EXAM_TIPS
schemas.py   Pydantic. Grounding: TopicSpec / LearningObjective. Output: ClassNotes,
             NoteSection, Callout, Diagram, KeyTerm, WorkedExample, PracticeQuestion,
             LOCoverage, NotesExtras; plus ImageChoice. Field descriptions are sent
             to Gemini as response_schema — edit them deliberately.
helpers.py   client + retry (call_model -> response.parsed); pipeline stages; image
             search + vision; renderers.
prompts/     outline / write_section / finalize / verify / spec_extract (read at call time)
curriculum/  the grounding store (TopicSpec JSON per topic)
```

`generate_notes(client, spec)` orchestrates (all structured output via `call_model`):

1. **outline** (`generate_outline`) — plan sections covering every objective;
   assign each objective to ONE section (avoids overlap).
2. **write sections** (`write_sections` → `write_section`) — draft each section
   in parallel (ThreadPool), each given the full outline so they don't re-teach
   one another. Yields body + inline callouts + diagrams + worked examples.
3. **images** (`fetch_images_for_sections`) — per `image`-kind diagram:
   `_search_images` (Wikimedia Commons with query simplification, Openverse
   fallback; CC/PD only) → `_select_image` (Gemini **vision** picks the best or
   rejects all) → width-capped thumbnail → base64 embed + `_attribution`. Mutates
   the diagrams in place.
4. **finalize** (`finalize_notes`) — overview, key terms, misconceptions, exam
   tips, practice, summary over the drafts.
5. **verify** (`verify_coverage`) — per-objective coverage audit (callouts
   included) + review flags for doubtful / off-depth content.

Then `save_notes` writes json + html. The `.json` is the source of truth; the
`.html` embeds that JSON inline and renders it client-side (its JS `buildMarkdown`
mirrors the Python `render_markdown`, kept as reference + `_smoke.py` oracle), so it
opens straight from disk. No `.md` file is written.

## Rendering rules (helpers.py `render_markdown` + `_HTML_SHELL`)

The `.html` embeds the structured ClassNotes JSON inline and renders it in the
browser: JS `buildMarkdown` (a port of `render_markdown`) assembles the Markdown,
then `marked` renders it, MathJax renders LaTeX, Mermaid renders flowcharts (all
CDN); images are base64 data URIs carried in the JSON. **Two renderers exist — Python
`render_markdown` (reference / smoke oracle) and the shell's JS `buildMarkdown`
(live) — keep them in sync.** Each rule below fixes a real bug — **don't regress
them** (and re-run `_smoke.py`, which asserts most of them):

- **Maths delimiters:** inline is `\(...\)`, display is `$$...$$`. A bare `$` is
  currency. MathJax is configured for `\(...\)` (NOT `$...$`) so `$80` never
  becomes maths. Because `marked` would strip the `\(` backslashes, math spans are
  **pulled out before `marked` and reinserted after** (the `__MATH__` protect step).
- **`_clean_md`** unescapes stray `\n` / `\t` the model sometimes emits, but
  **protects math spans** so LaTeX beginning with `\n`/`\t` (`\neq`, `\to`,
  `\text`) survives. Applied to all model-produced text fields.
- **`_sanitize_mermaid`** quotes `[...]` node labels (`A[Glucose (6C)]` →
  `A["Glucose (6C)"]`) so parentheses/`+` don't break Mermaid; invalid diagrams
  fall back to a soft source box (never the error "bomb").
- **Callouts** are blockquotes led by an emoji; a JS colouriser tags each by that
  emoji (💡 tip / ⚠️ mistake / 📐 formula / 🧠 remember / 🎯 strategy) → CSS box.
- **Collapsible sections:** every major section is a `<details class="topic">`
  (`_fold_open` / `_fold_close`), collapsed by default; only title + overview are
  front matter. The blank-line trick lets Markdown render inside `<details>`.
  Because sections start collapsed, **Mermaid renders on first expand** (a diagram
  in a hidden `<details>` sizes to 0) via a `toggle` listener; MathJax typesets at
  load (fine while hidden).
- **Exam strategy:** `BOARD_EXAM_TIPS[level]` (curated, accurate exam-format facts)
  is merged with the topic's `exam_tips` into ONE 🎯 box — there is no separate
  "Exam tips" section.
- **Header subtitle** is `board · subject · level — unit`, but the standalone
  `level` is **dropped when `board` already contains it** (e.g. board
  `Edexcel A-Level` + level `A-Level` → prints "A-Level" once, not twice).
- **Objective tier tag:** the per-objective `_(tier)_` suffix (Core/Supplement) is
  **suppressed when `tier == level`** — it's redundant noise, not a depth tag. Fix
  the source data too (a curriculum spec should not set `tier` to its own level).
- **Prose "diagrams" are hidden from students:** only real visuals render inline
  (`mermaid`, `latex`, `image` *with* a fetched `image_src`). A `kind:"description"`
  stub, or an `image` whose search found nothing, is **collected into the teacher/QA
  footer** ("Illustrations to add"), never shown inline as a broken-looking
  "Diagram —" blockquote. (Generation is also steered away from `description`.)
- **Teacher/QA footer:** coverage count, generated **date** (not the raw ISO
  timestamp), review flags, and the illustration stubs above live in ONE collapsed
  `<details>` ("For teachers · QA") at the end — **not** as inline student-facing
  text. The JSON remains the full internal record.
- **Practice questions carry `difficulty` + `marks`:** each renders a
  `_(basic · 4 marks)_`-style tag; the unit is board-appropriate — **"points" when
  `level == "AP"`**, else "marks", and omitted when `marks` is null (e.g. SAT). The
  set is a difficulty ladder (basic → stretch, ~5–6 questions) and each solution is a
  mark scheme (M1 / A1 …). Both fields live on `PracticeQuestion`.
- **Section spec codes are shown:** each section renders a `*Spec points: …*` line
  from `covers_objective_codes` (a coverage map / rigour signal). This is the ONE
  place codes surface — the Learning-objectives list still shows statements only.
- **Foundational visuals:** a topic's core diagram (e.g. an exothermic/endothermic
  reaction energy profile in energetics) must render as a real `mermaid`/`image`, not
  prose — the `outline` / `write_section` prompts steer this. ("Links to other topics"
  is deferred until the corpus has sibling notes to link.)

## Conventions specific to this repo

- **Interpreter:** run with `py -3`. Bare `python` on this machine is a different
  Python 3.14 without the deps (`ModuleNotFoundError: pydantic`); `py -3` resolves
  to the pythoncore-3.14 install that has google-genai / pydantic / pillow.
- **Grounding is the moat.** Never let the model invent exam-format facts or exceed
  the depth profile. Curated facts (exam strategy, hand-seeded specs) are
  hand-authored and tagged "validate against the official spec".
- **Curriculum is self-describing.** Add a topic = drop a `curriculum/<id>.json`;
  no code change. New board's exam strategy = a `BOARD_EXAM_TIPS[level]` entry.
- **Schema field `description=`s are prompt surface** — sent to Gemini as
  `response_schema`; edit deliberately, not just for documentation.
- **Prompts are plain text**, `str.format`-ed at call time. Avoid literal `{`/`}`
  in the template text (LaTeX braces inside *injected values* are safe — only the
  template's own braces are parsed).
- **Images must stay licence-safe** (Wikimedia / Openverse, CC/PD only, with
  attribution) — this is a commercial product; do not scrape Google Images or
  embed unlicensed art. Gemini vision rejects off-topic/inappropriate candidates.
- **Windows console:** `notes.py` reconfigures stdout to UTF-8. Ad-hoc
  `py -3 -c "print(...)"` one-liners with emoji hit cp1252 — reconfigure stdout in
  the snippet, or avoid emoji in console output.
- **Never commit** `out/*` (except `.gitkeep`), `.env`, or `.secrets/` — gitignored.
  `.claude/` (incl. the `launch.json` used for the HTML preview server) stays
  local / untracked.
```

