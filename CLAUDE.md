# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

A curriculum-grounded **class-notes generator** for AP / IGCSE / SAT / A-Level.
Feed a topic id; a multi-stage Gemini pipeline (outline → draft sections → fetch
images → finalize → verify coverage) renders self-contained notes to
`out/<topic_id>.{md,html,json}`. Sister project to `../Grader` — same
single-vendor Gemini stack and `config / schemas / helpers / prompts` split; the
notes are grounded in the same curriculum the Grader assesses against.

## Commands

Use **`py -3`**, NOT bare `python` (see Conventions):

```bash
py -3 notes.py --list           # list discovered topics
py -3 notes.py <topic_id>       # generate one topic (live Gemini calls)
py -3 notes.py --all            # generate every topic
py -3 _smoke.py                 # OFFLINE render self-test (stubs Gemini; no API key, no network)
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

Then `render_markdown` → `render_html` → `save_notes` writes md/html/json.

## Rendering rules (helpers.py `render_markdown` + `_HTML_SHELL`)

Self-contained HTML: `marked` renders the Markdown, MathJax renders LaTeX, Mermaid
renders flowcharts (all CDN); images are base64 data URIs. Each rule below fixes a
real bug — **don't regress them** (and re-run `_smoke.py`, which asserts most of them):

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

