# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

A curriculum-grounded **class-notes generator** for AP / IGCSE / SAT / A-Level / AMC.
Feed a topic id; a multi-stage Gemini pipeline (outline → draft interactive section
blocks → enforce coverage → fetch images → practice ladder → finalize) writes an
INTERACTIVE lesson to `out/<board>/<subject>/<topic_id>.v2.json` (the source of truth)
+ a sibling `<topic_id>.interactive.html` (a self-contained page that embeds that JSON
and renders it client-side — opens straight from disk, no server). Sister project to
`../Grader` — same single-vendor Gemini stack and `config / schemas / helpers /
prompts` split; the notes are grounded in the same curriculum the Grader assesses
against.

> The old v1 Markdown-in-`<details>` format (`ClassNotes`, `render_markdown`,
> `out/<id>.{json,html}`) has been **retired** — the interactive block format is the
> sole output. `schemas.py` / `helpers.py` now hold only the pieces the v2 pipeline
> shares (grounding types, the Gemini client, the outline stage, image search); the
> assembled output type + renderer live in `schemas_v2.py` / `render_v2.py`.

## Commands

Use **`py -3`**, NOT bare `python` (see Conventions):

```bash
py -3 src/notes.py --list                 # list discovered topics
py -3 src/notes.py <topic_id>             # generate one topic (live Gemini calls)
py -3 src/notes.py --all                  # generate all topics — skips existing + UNVERIFIED, isolates
                                          #   per-topic failures, parallel (--jobs). --force / --dry-run.
py -3 src/notes.py --subject Chemistry --dry-run   # plan a board/subject/level slice; ZERO Gemini calls
py -3 src/notes.py --status [--watch 5]            # live progress dashboard (read-only; no key/network)
py -3 src/extract_specs.py --list         # curriculum-extraction CLI: official spec/CED PDF -> many TopicSpecs
                                          #   (--board/--subject or --all; DRY RUN by default; --apply writes)
py -3 src/ground_specs.py --list          # spec-grounding CLI: verify codes vs official spec PDFs
                                          #   (DRY RUN by default; --apply writes; --all = corpus)
py -3 src/approve_specs.py --list         # clear the UNVERIFIED marker after review (fetch -> generate handoff)
py -3 src/spotcheck.py                    # bundle a deterministic ~1-in-20 tutor spot-check into out/spotcheck/
py -3 tests/_smoke_v2.py                  # OFFLINE self-test — no key/network
```

**Fetch and generate are separate, repeatable commands.** The curriculum store is
**grown from the source of record** (fetch): `extract_specs.py` fetches a subject's official
spec/CED PDF, enumerates its topics, and extracts one grounded `TopicSpec` per topic into
`curriculum/`. Extraction is PDF-grounded but **stamped UNVERIFIED** — the fetch pipeline is
`extract_specs --apply` → `ground_specs --apply` (verify codes vs the SAME PDF) → review
`git diff` → `approve_specs --apply` (clear the marker) → then `notes.py` will generate it.
It only pulls (board, subject) pairs registered in `sources._SPEC_SOURCES`; widen by adding entries.

**Generation** (`notes.py`) then runs over `curriculum/` again and again. `--all` is a production
batch runner: **skips already-generated output** (`--force` to regenerate) and **UNVERIFIED specs**
(`--include-unverified` to include), **isolates per-topic failures** (one bad topic never aborts the
run — outcomes land in `out/run-manifest.json`), accepts `--board`/`--subject`/`--level` selectors,
and generates topics **in parallel** (`--jobs`, default `CONFIG["max_parallel_topics"]`). Parallelism
is pure — same models/stages/gates, only overlapped — bounded globally by
`CONFIG["max_inflight_model_calls"]` (a semaphore in `helpers.call_model`), so it never trades quality
for speed. Batch pure logic lives in `batch.py` (genai-free).

Offline self-tests live in `tests/` (no key/network), each the fast regression check for its area:
`tests/_smoke_v2.py` (renderer↔schema parity + coverage/structural gate + prompt brace-safety),
`tests/_smoke_past_papers.py` (two-pass + URL/render safety), `tests/_smoke_ground_specs.py`
(confidence gating + in-place patch), `tests/_smoke_extract_specs.py` (id convention + skip-existing
+ UNVERIFIED stamping + cross-module gate sync), `tests/_smoke_notes_batch.py` (select/plan/provenance
gate + manifest + tagged output), `tests/_smoke_spotcheck.py` (deterministic sampling).
**Run the relevant one after touching its module.**

**Re-render existing notes without regenerating** (apply render/CSS changes, no API):
```bash
py -3 -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from schemas_v2 import InteractiveNotes; from pipeline_v2 import save_interactive_notes; [save_interactive_notes(InteractiveNotes.model_validate_json(Path(p).read_text(encoding='utf-8'))) for p in sorted(Path('out').rglob('*.v2.json'))]"
```
Everything (including embedded `image_src` base64) is stored in the JSON, so
`render_interactive_html` is a pure function of the saved `InteractiveNotes` —
render-layer changes apply instantly to all notes with no Gemini calls. Prefer this
over `--all` when you only touched rendering.

## Interactive format (block-based; the sole output)

A single-scroll INTERACTIVE lesson: progress tracking, flip-card definitions, MCQs
with per-option feedback, step-reveal worked examples, live sims, drag-to-bucket
sorts, parameterized energy-profile / Hess SVGs, a numeric practice ladder with
tolerance + diagnostic wrong-answers + mark schemes, a spec checklist, and a curated
exam-map + past-paper panel. `notes.py <id>` writes `out/<board>/<subject>/<id>.v2.json`
+ a sibling `<id>.interactive.html`. Target design contract: `enthalpy-interactive-full.html`.

- `schemas_v2.py` — the block vocabulary: an ordered list of typed `Block`s per
  `InteractiveSection` (prose / callout / table / flip_cards / mcq / step_reveal /
  numeric / sim / sort / toggle_diagram / cycle_diagram / reveal / accordion /
  figure) + the assembled `InteractiveNotes`. The union is a **plain `Union`** (→
  `anyOf`), NOT discriminated — the Vertex schema transformer rejects
  `oneOf`/`discriminator`. Blocks carry NO id; the renderer assigns ids by position.
  Reuses grounding/shared types from `schemas.py` (`LearningObjective`, `Diagram`,
  `LOCoverage`, `ExamMapCell`, `SpecChecklistItem`, `PastPapers`).
- `render_v2.py` — ONE client-side block-dispatch renderer (`renderBlock`) +
  `render_interactive_html` + `validate_interactives` (deterministic post-gen checks
  → `review_flags`). See the safety doctrine below.
- `pipeline_v2.py` — `generate_interactive_notes`: outline → section blocks
  (parallel) → **enforce coverage** → images for `figure` blocks → practice ladder →
  finalize (hero / hook / command words / mistakes / checklist recaps) → assemble.
  Prompts: `prompts/v2_{write_section,write_practice,finalize}.txt`. **Prompt
  templates are `str.format`-ed — never put literal `{`/`}` in them (a `{-1}` unit
  example once broke every section-write with `KeyError`; `_smoke_v2.py` now asserts
  every prompt formats cleanly).**
- Curated per topic: `TopicSpec.exam_map / spec_checklist / next_topic` (+ optional
  hand-verified `past_papers`, which the pipeline leaves untouched). **Past papers are
  otherwise generated** (`past_papers.py` + `sources.py`): fetch the real paper PDF from
  the per-board registry → propose citations from it → independently verify each against
  the SAME PDF (two-pass) → keep only confirmed. The `url` is always the registry url
  (never model text) and `verified[]` renders via `textContent` + a `safeUrl` guard; no
  lawful paper source ⇒ resources-only signposting. Never recall a citation from memory.
- Spec/LO codes are likewise grounded: `ground_specs.py` (standalone CLI) verifies each
  hand-seeded code against the official spec/CED PDF from `sources.py` and auto-corrects
  only high-confidence mismatches in place (dry-run default; `curriculum/` is git-tracked,
  so `git diff` is the review backstop; 'absent' is report-only, never auto-deleted).
- **Parity contract** (asserted by `_smoke_v2.py`): the block `type` set in
  `schemas_v2.BLOCK_TYPES` === the `renderBlock` `case` set in the JS — keep them in
  lockstep. Keeps MathJax + `marked` (prose only) + a Google-Fonts link; no Mermaid.

## Enforcement gates — tiered by trust (deterministic FACTS block; model OPINIONS advise)

The pipeline separates what it **gates** on from what it merely **flags** by the
**source** of the signal, because they have different reliability:

- **Deterministic checks are FACTS → they gate (fix-or-fail).** Two tiers:
  1. **Coverage.** `enforce_coverage_v2` (`pipeline_v2.py`) runs the audit and, for any
     objective the verifier marks `covered:false` — or that a command word demands an
     artifact for but lacks it (the structural-evidence rules in `coverage_gate.py`:
     `prove → step_reveal`, `calculate → numeric/sim`) — **regenerates the owning
     section(s)** with the `gap_note` injected, re-verifies, up to
     `CONFIG["max_coverage_retries"]`; a surviving gap raises `CoverageError`.
  2. **Block completeness.** The SAME section loop also runs `render_v2.block_defects`
     (an unexplained MCQ option, a numeric with no mark scheme, an empty worked example,
     a dead widget) and regenerates the owning section for any defect — so a structural
     fix is re-verified for coverage in the same loop. The practice ladder gets the same
     check in its own loop (`enforce_practice_structure_v2`, `max_structure_retries`). A
     surviving defect raises `StructuralError`. A final `block_defects` guard at
     assemble time refuses to write if anything slipped a gate.
- **The model verifier's `review_flags` are OPINIONS → they DON'T gate.** They land in
  `notes.review_flags` (advisory) and are surfaced to the human `spotcheck.py` queue. A
  single model read can be wrong — it can flag a *complete* block as incomplete — so a
  blanket "regenerate on any flag" would burn calls fixing phantom defects and could
  replace good work with worse. Gating that class is the deterministic tier's job.

If any gate fails, **nothing is written** (`notes.py` reports it and exits non-zero;
`--all` lists the failed topics and continues). All gate logic is pure and genai-free
(`coverage_gate.py` + `render_v2.block_defects`), so `_smoke_v2.py` exercises every tier
without a key. The coverage audit runs at writer strength via `CONFIG["model_coverage"]`
(a weak auditor rubber-stamps — the exact failure it guards against) and sees only the
notes + contract, never the writer's reasoning, so it stays an independent read.

## Setup contract

- `.env` with ONE auth method (same as the Grader — its key works here):
  - `GEMINI_API_KEY=...` (AI Studio), or
  - `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` (Vertex AI).
  `get_gemini_client()` prefers the key and falls back to Vertex.
- **Optional cost tracking:** set `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`)
  in `.env` and every Gemini call is logged to Langfuse as a generation with token usage —
  it prices the models itself, so total/per-subject/per-stage cost rolls up (grouped by a
  deterministic trace per `topic_id`). The hook is in `helpers.call_model` (the single call
  site); it is a strict no-op when the keys are absent and NEVER breaks a run. `pip install langfuse`.
- `curriculum/*.json` — one `TopicSpec` per topic, self-describing (carries its
  own board/subject/level); discovered automatically by `discover_topics()`. Author
  them by hand, or grow the store from official spec PDFs with `extract_specs.py`
  (then verify with `ground_specs.py` + review the `git diff` before generating).

## Architecture & data flow

```
src/             the application — run: py -3 src/notes.py <id>
  config.py        CONFIG (models, temps, coverage + structure gates, image settings), HOUSE_STYLE,
                   BOARD_EXAM_TIPS, BOARD_SUBJECT_EXAM_TIPS, exam_tips_for(level, subject)
  schemas.py       Pydantic. Grounding: TopicSpec / LearningObjective. Shared output parts: Diagram,
                   ExamMapCell, PastPapers, SpecChecklistItem, LOCoverage / CoverageReport, ImageChoice,
                   OutlineSection / NotesOutline. Field descriptions are Gemini response_schema surface.
  schemas_v2.py    the v2 block vocabulary + the assembled InteractiveNotes
  helpers.py       shared utilities: Gemini client + retry (call_model -> response.parsed, with a global
                   in-flight concurrency governor), _gen_config, load_prompt, grounding (_spec_block),
                   the outline stage, image search + vision
  coverage_gate.py deterministic, genai-free gate logic — coverage (CoverageError) + block-completeness
                   tier (StructuralError, defect_feedback_by_section, structural_feedback_block)
  batch.py         deterministic, genai-free batch-runner logic — TopicResult + outcome vocabulary,
                   select_specs / plan_batch (skip-existing + UNVERIFIED gate), manifest, TaggedStdout
  sources.py       curated per-board source registry (paper/spec PDF URLs) + resolve_sources
  past_papers.py   PDF-grounded past-paper stage (two-pass: generate + verify against the fetched PDF)
  pipeline_v2.py   the pipeline: generate_interactive_notes + save_interactive_notes
  render_v2.py     the interactive renderer (render_interactive_html) + block_defects / validate_interactives
                   (the deterministic block-completeness checks the structural gate enforces)
  extract_specs.py standalone CLI: official subject spec/CED PDF -> many TopicSpec JSONs (enumerate topics ->
                   extract one grounded spec each; DRY RUN default; stamps every spec UNVERIFIED for review)
  ground_specs.py  standalone CLI: verify + auto-correct curriculum codes vs official spec/CED PDFs
  approve_specs.py standalone CLI: clear the UNVERIFIED marker on reviewed specs (fetch -> generate handoff;
                   DRY RUN default; --apply writes) — the human-trust step notes.py gates on
  spotcheck.py     standalone CLI: deterministic 1-in-20 tutor spot-check bundle (surfaces each page's
                   advisory review_flags for human adjudication)
  notes.py         CLI entry point + batch runner (run_one -> TopicResult; selectors; skip-existing;
                   --jobs parallelism; per-topic failure isolation; out/run-manifest.json)
  prompts/         outline / verify / v2_* / past_papers_* / spec_ground / spec_extract / spec_enumerate
tests/           offline self-tests (_smoke_*.py; no key/network)
curriculum/      the grounding store (TopicSpec JSON per topic; hand-authored or extract_specs-grown)
out/             generated notes, grouped <board>/<subject>/<id>.{v2.json,interactive.html} (gitignored)
```

`generate_interactive_notes(client, spec)` orchestrates (all structured output via
`call_model`):

1. **outline** (`helpers.generate_outline`) — plan sections covering every
   objective; assign each objective to ONE section (avoids overlap).
2. **write sections** (`write_sections_v2` → `write_section_v2`) — draft each
   section in parallel (ThreadPool) as a list of typed interactive blocks, each given
   the full outline so they don't re-teach one another.
3. **enforce coverage** (`enforce_coverage_v2`) — verify → regenerate uncovered
   sections → re-verify → hard-fail if a gap survives (see Coverage above). Runs
   BEFORE images/practice/finalize so those stages never build on doomed sections.
4. **images** (`fetch_images_for_blocks`) — per `figure` block (kind `image`):
   `helpers._search_images` (Wikimedia w/ query simplification, Openverse fallback;
   CC/PD only) → `helpers._select_image` (Gemini **vision** picks best or rejects) →
   base64 embed + `_attribution`. Mutates the figure diagrams in place.
5. **practice** (`write_practice_v2`) — a 5–6 question numeric/mcq ladder
   (basic → stretch) with tolerances, diagnostic wrong-answers and mark schemes, then
   the **practice structural gate** (`enforce_practice_structure_v2`) fixes-or-fails any
   block defect (missing mark scheme, unexplained option) in the ladder.
6. **finalize** (`finalize_v2`) — hero, hook, command words, common mistakes,
   spec-checklist recaps. Assembly ends with a `block_defects` guard; `review_flags`
   then carry ONLY the model verifier's advisory opinions (deterministic FACTS have
   already been fixed-or-failed by the gates — see Enforcement gates above).

Then `save_interactive_notes` writes the `.v2.json` (source of truth) +
`.interactive.html` (embeds the JSON, rendered client-side by `render_v2._JS`) into
`out/<board>/<subject>/` (both segments filesystem-sanitised by `_fs_safe`).

## Rendering & safety doctrine (render_v2.py)

The `.interactive.html` embeds the `InteractiveNotes` JSON inline and renders it via
ONE data-driven dispatcher (`renderBlock`). Each rule fixes a real bug — **don't
regress** (`_smoke_v2.py` asserts most):

- **The renderer owns geometry + arithmetic.** SVG diagrams (`SVG_TEMPLATES`, e.g.
  `energy_profile`) and sim/qline math (a tokenized evaluator, never `eval`) are
  computed by the renderer from labelled/parameterized model input — the model never
  authors markup or formulas that reach the DOM as code.
- **Model strings via `textContent`, never `innerHTML`** — the single exception is
  the `prose` block, which goes through `marked`. Any NEW field that renders model
  text must use `textContent`; embedded JSON escapes `<` to `<` so a stray
  `</script>` in a field can't close the data block.
- **Maths delimiters:** inline `\(...\)`, display `$$...$$`. A bare `$` is currency;
  MathJax is configured for `\(...\)` (NOT `$...$`) so `$80` never becomes maths.
- **Parity:** `schemas_v2.BLOCK_TYPES` === the `renderBlock` `case` set (asserted).
- **Codes:** section `spec_label` + the spec checklist surface spec-point codes; the
  internal objective codes never appear in student-facing prose.
- **Exam strategy:** `exam_tips_for(level, subject)` — the board-general
  `BOARD_EXAM_TIPS[level]` plus any `BOARD_SUBJECT_EXAM_TIPS[(level, subject)]` overlay
  (so SAT Reading & Writing doesn't inherit SAT Math's Desmos/grid-in facts) — grounds the
  per-section exam pointers; keep them consistent with the real format.

## Conventions specific to this repo

- **Interpreter:** run with `py -3`. Bare `python` on this machine is a different
  Python 3.14 without the deps (`ModuleNotFoundError: pydantic`); `py -3` resolves
  to the pythoncore-3.14 install that has google-genai / pydantic / pillow.
- **Grounding is the moat.** Never let the model invent exam-format facts, spec
  codes, or past-paper citations, or exceed the depth profile. A model claim becomes
  a shippable fact only when it traces to a fetched document or a deterministic check
  — never recall. Curated facts are hand-authored and tagged "validate against the
  official spec". This extends to **curriculum extraction** (`extract_specs.py`):
  objectives are pulled from the fetched spec PDF (never memory), the controlled
  identity fields (board/level/id) are stamped deterministically not model-guessed,
  the curated exam-format layer is left empty for a human, and every extracted spec is
  marked UNVERIFIED. That marker is a **generation gate**: `notes.py` skips UNVERIFIED
  specs (`batch.is_unverified`) so an un-reviewed extract can't ship notes. It is cleared
  only by `approve_specs.py` — the explicit human-trust step AFTER `ground_specs.py` +
  `git diff` review — never automatically (grounding is automated, so clearing it there
  would collapse the gate). `_smoke_extract_specs.py` asserts the stamp and the gate token stay in sync.
- **Curriculum is self-describing.** Add a topic = drop a `curriculum/<id>.json`;
  no code change (hand-author it, or extract it with `extract_specs.py`). New board's
  exam strategy = a `BOARD_EXAM_TIPS[level]` entry (add a `BOARD_SUBJECT_EXAM_TIPS[(level,
  subject)]` entry when the facts differ by subject) + a `BOARD_TO_LEVEL` mapping if the
  board is new. A new (board, subject) becomes extractable by adding its official spec
  PDF to `sources._SPEC_SOURCES`.
- **Schema field `description=`s are prompt surface** — sent to Gemini as
  `response_schema`; edit deliberately, not just for documentation.
- **Prompts are plain text**, `str.format`-ed at call time. Avoid literal `{`/`}`
  in the template text (LaTeX braces inside *injected values* are safe — only the
  template's own braces are parsed). `_smoke_v2.py` asserts every prompt formats.
- **Images must stay licence-safe** (Wikimedia / Openverse, CC/PD only, with
  attribution) — this is a commercial product; do not scrape Google Images or
  embed unlicensed art. Gemini vision rejects off-topic/inappropriate candidates.
- **Windows console:** `notes.py` reconfigures stdout to UTF-8. Ad-hoc
  `py -3 -c "print(...)"` one-liners with emoji hit cp1252 — reconfigure stdout in
  the snippet, or avoid emoji in console output.
- **Never commit** `out/*` (except `.gitkeep`), `.env`, or `.secrets/` — gitignored.
  `.claude/` (incl. the `launch.json` used for the HTML preview server) stays
  local / untracked.
