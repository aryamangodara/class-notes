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
py -3 src/approve_specs.py --list         # MANUAL OVERRIDE: specs the curriculum gate declined, + why
py -3 src/audit_citations.py              # re-check SHIPPED past-paper citations vs the real PDFs (no model calls)
py -3 src/spotcheck.py                    # bundle a deterministic ~1-in-20 tutor spot-check into out/spotcheck/
py -3 tests/_smoke_v2.py                  # OFFLINE self-test — no key/network
```

**Fetch and generate are separate, repeatable commands, and NOTHING waits for a human.**
The curriculum store is **grown from the source of record** (fetch): `extract_specs.py`
fetches a subject's official spec/CED PDF, enumerates its topics, and extracts one grounded
`TopicSpec` per topic into `curriculum/` — holding each to the **curriculum gate**
(`spec_gate.py`) in the same pass: every code and every objective's `evidence_quote` must be
located in that same PDF, else it re-extracts with the gaps injected
(`max_spec_repair_retries`) and then stays UNVERIFIED. So the pipeline is just
`extract_specs --apply` (extract + verify + auto-approve) → `notes.py`.
It only pulls (board, subject) pairs registered in `sources._SPEC_SOURCES`; widen by adding entries.

> **The human approval step is gone, and that made the gate STRONGER, not weaker.** It used
> to be `... → review git diff → approve_specs --apply`. But `deploy/run_all.sh` ran
> `approve_specs --apply` as an unconditional phase, so the marker was wiped with nothing
> checking anything (0 of 103 specs were unverified) — a rubber stamp wearing a gate's name.
> It is now cleared only by evidence a model cannot talk its way past. `approve_specs.py`
> survives ONLY as a manual override (`--force-approve`) for a spec you checked yourself and
> believe the gate got wrong. A human still curates `sources.py` and `BOARD_EXAM_TIPS`, still
> reads `git diff curriculum/`, and still adjudicates `spotcheck.py` — but nothing BLOCKS on
> any of it.

**Generation** (`notes.py`) then runs over `curriculum/` again and again. `--all` is a production
batch runner: **skips already-generated output** (`--force` to regenerate) and **UNVERIFIED specs**
(`--include-unverified` to include), **isolates per-topic failures** (one bad topic never aborts the
run — outcomes land in `out/run-manifest.json`), accepts `--board`/`--subject`/`--level` selectors,
and generates topics **in parallel** (`--jobs`, default `CONFIG["max_parallel_topics"]`). Parallelism
is pure — same models/stages/gates, only overlapped — bounded globally by
`CONFIG["max_inflight_model_calls"]` (a semaphore in `helpers.call_model`), so it never trades quality
for speed. Batch pure logic lives in `batch.py` (genai-free).

Offline self-tests live in `tests/` (no key/network), each the fast regression check for its area:
`tests/_smoke_v2.py` (renderer↔schema parity + coverage/structural/ladder/hook gates + marks-convention
JS parity + prompt brace-safety), `tests/_smoke_pdf_text.py` (the grounding primitive: normalisation
folds, contiguity discrimination, fail-closed extraction), `tests/_smoke_spec_gate.py` (the curriculum
gate: code/quote evidence, repair-vs-block boundary, evidence stripping, marker semantics),
`tests/_smoke_past_papers.py` (evidence gate + "null" refs + URL/render safety),
`tests/_smoke_audit_citations.py` (label parsing + static no-model-call / read-only guards),
`tests/_smoke_ground_specs.py` (confidence gating + in-place patch), `tests/_smoke_extract_specs.py`
(id convention + skip-existing + gate-verdict stamping + cross-module gate sync),
`tests/_smoke_notes_batch.py` (select/plan/provenance gate + manifest + tagged output),
`tests/_smoke_spotcheck.py` (deterministic sampling), `tests/_smoke_tracing.py` (the tracing doctrine:
static no-untraced-call scan + single-door + stage-vocabulary parity + tag/limit contract).
`tests/_pdfgen.py` is a shared fixture builder, not a test: it constructs a REAL parseable PDF in
pure stdlib (the old `b"%PDF-1.4 stub bytes"` cannot be parsed, so it could only ever exercise the
drop path once citations had to be PROVEN).
**Run the relevant one after touching its module.** `_smoke_tracing.py` scans `src/` statically,
so run it after adding ANY model call site.

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
- Curated per topic: `TopicSpec.spec_checklist` (+ optional hand-verified `past_papers`,
  which the pipeline leaves untouched). **Past papers are otherwise generated**
  (`past_papers.py` + `sources.py`) through THREE checks: fetch the real paper PDF from the
  per-board registry → propose citations from it → independently verify each against the
  SAME PDF → **deterministically require each survivor's `evidence_quote` to be present in
  that PDF's extracted text** (`pdf_text.quote_supported`).
  **That third check is the one that makes "verified" mean anything.** The first two are
  both model calls, and model-checking-model fails in the same direction — which is how a
  fabricated `Paper 1 · Q2(c)(i)` with an invented ionisation-energy table once shipped as
  verified. A citation that fails is dropped INDIVIDUALLY (siblings still ship); a scanned
  PDF with no text layer yields NO citations at all, and bails before the model calls.
  `confirmed_to_verified` takes `paper_text` **keyword-only with no default** — the same
  enforcement shape as `call_model`'s `trace`, so a new call site cannot silently reopen
  the hole. The `url` is always the registry url (never model text), `verified[]` renders
  via `textContent` + a `safeUrl` guard, and a placeholder question ref (`"null"`, `"n/a"`,
  …) is rejected — those strings are TRUTHY, so the old `if q else ""` guard rendered
  `June 2023 · Paper 1 · null · 2 marks` to students. No lawful paper source ⇒
  resources-only signposting. Never recall a citation from memory.
  `evidence_quote` is deliberately NOT persisted to `.v2.json` (it is verbatim exam text —
  a licensing problem in a shipped page), so the offline `audit_citations.py` probes the
  OTHER half instead: the question ref, via `pdf_text.locate_question`. Content grounding
  catches invented CONTENT; identity grounding catches invented QUESTION NUMBERS; neither
  alone catches both. That probe is **advisory only** — it walks real two-level exam
  structure (AP prints `3.` then `(b)` then `(ii)`; Edexcel opens with a tab) and a naive
  line-prefix version was measured wrong in BOTH directions on 3 of 5 genuine citations.
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

- **Deterministic checks are FACTS → they gate (fix-or-fail).** Three tiers, in the order
  a topic meets them:
  0. **Curriculum** (`spec_gate.py`, at extraction time — see the fetch section above).
     Every code and every objective's `evidence_quote` must be located in the spec PDF the
     extraction read, else re-extract with the gaps injected (**re-slicing on the failing
     codes**, or attempt 2 re-asks the same question of the same pages — a
     self-*confirmation* loop) up to `CONFIG["max_spec_repair_retries"]`, then stay
     UNVERIFIED. Failure mode differs from the other two: **ungenerable, not unwritten.**
     Costs no extra model calls — the quotes come back from the extraction that was
     already happening. Plus `min_objectives_per_topic` (default **1**) as a partial
     backstop against silent under-extraction — it catches a spec that extracted NOTHING.
     **Do not raise it without checking the board's structure:** the AP CED prints exactly
     ONE `LEARNING OBJECTIVE` per topic (`1.1.A`) with several `ESSENTIAL KNOWLEDGE` points
     beneath it (`1.1.A.1/.2/.3`) that belong in `depth_profile`, so 1 is correct there. At
     a floor of 2 this flagged 53 correct AP specs, and the repair loop "fixed" them by
     promoting Essential Knowledge into `learning_objectives` — a schema violation that
     reads as an improvement in a diff.
  1. **Coverage.** `enforce_coverage_v2` (`pipeline_v2.py`) runs the audit and, for any
     objective the verifier marks `covered:false` — or that a command word demands an
     artifact for but lacks it (the structural-evidence rules in `coverage_gate.py`:
     `prove → step_reveal`, `calculate → numeric/sim`) — **regenerates the owning
     section(s)** with the `gap_note` injected, re-verifies, up to
     `CONFIG["max_coverage_retries"]`; a surviving gap raises `CoverageError`.
  2. **Block completeness.** The SAME section loop also runs `render_v2.block_defects`
     (an unexplained MCQ option or two options sharing one explanation, a numeric with no
     mark scheme / no diagnostic `wrong_answers` / a mark scheme that does not add up to
     `marks`, an empty worked example, an EMPTY CONTAINER — `flip_cards` with no cards,
     `table` with no rows, `accordion` with no items, `reveal` with no answer — or a dead
     widget) and regenerates the owning section for any defect, so a structural fix is
     re-verified for coverage in the same loop. **Three loops, one per locus**: sections
     here, the ladder in `enforce_practice_structure_v2`, and the hook in
     `enforce_finalize_structure_v2` (the hook belongs to no section, so before it had its
     own loop a hook defect hard-failed at the assemble guard with ZERO retries). Set-level
     rules that only exist over a whole collection live in `practice_set_defects` and share
     the `practice` locus — **push every rule down to the smallest locus some stage can
     regenerate**; a "document" locus could only ever hard-fail, because nothing regenerates
     a document. A surviving defect raises `StructuralError`, and a final `document_defects`
     guard at assemble time refuses to write if anything slipped a gate.
     - The **coverage and structural budgets are tracked separately** (`cov_attempt` /
       `struct_attempt`). They shared one counter, so a topic with both faults spent its
       coverage attempts on the block — adding structural rules would have silently
       weakened the stronger gate.
     - An empty `flip_cards` block used to SATISFY the define/state structural-evidence
       rule while teaching nothing. That is why empty containers are a FACT, not a flag.
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
- **Cost tracking (Langfuse):** set `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`)
  in `.env` and every Gemini call is logged as a generation with token usage — Langfuse prices
  the models itself, so total / per-feature / per-subject / per-stage cost rolls up. The hook is
  in `helpers.call_model` (the single call site); it is a strict no-op when the keys are absent
  and NEVER breaks a run. `pip install langfuse`. See **Tracing doctrine** below for the contract
  every call must satisfy.
- `curriculum/*.json` — one `TopicSpec` per topic, self-describing (carries its
  own board/subject/level); discovered automatically by `discover_topics()`. Author
  them by hand, or grow the store from official spec PDFs with `extract_specs.py`, which
  verifies and approves each one against that PDF in the same pass. `git diff curriculum/`
  is a post-hoc audit trail — nothing blocks on reading it.

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
                   in-flight concurrency governor), the tracing contract (FEATURES / STAGES /
                   trace_for / trace_topic / trace_spec_run + the Langfuse hook — see Tracing
                   doctrine), _gen_config, load_prompt, grounding (_spec_block), the outline
                   stage, image search + vision
  coverage_gate.py deterministic, genai-free gate logic — coverage (CoverageError) + block-completeness
                   tier (StructuralError, defect_feedback_by_section, structural_feedback_block)
  spec_gate.py     deterministic, genai-free CURRICULUM gate — code/quote evidence vs the spec PDF,
                   shape gaps, approve/repair/block decision, spec_feedback_block, strip_evidence
  pdf_text.py      LEAF (stdlib + soft fitz): the deterministic grounding primitive. normalise ->
                   longest_common_run -> quote_supported ("is this snippet provably in THAT PDF?"),
                   plus locate_question for the offline citation audit. Fails CLOSED on a scan.
  batch.py         deterministic, genai-free batch-runner logic — TopicResult + outcome vocabulary,
                   select_specs / plan_batch (skip-existing + UNVERIFIED gate), manifest, TaggedStdout
  sources.py       curated per-board source registry (paper/spec PDF URLs) + resolve_sources
  past_papers.py   PDF-grounded past-paper stage (propose -> model-verify -> DETERMINISTIC evidence check)
  pipeline_v2.py   the pipeline: generate_interactive_notes + save_interactive_notes
  render_v2.py     the interactive renderer (render_interactive_html) + block_defects / practice_set_defects
                   / hook_block_defects / document_defects (the deterministic checks the gates enforce)
  extract_specs.py standalone CLI: official subject spec/CED PDF -> many TopicSpec JSONs (enumerate topics ->
                   extract one grounded spec each, held to spec_gate with a re-extract repair loop;
                   DRY RUN default; auto-approves on evidence, else stamps UNVERIFIED with the reason)
  ground_specs.py  standalone CLI: verify + auto-correct curriculum codes vs official spec/CED PDFs
                   (an OPT-IN deeper model audit; no longer the approver)
  approve_specs.py standalone CLI: MANUAL OVERRIDE (--force-approve) for a spec the curriculum gate
                   declined — not part of any automated path
  audit_citations.py standalone CLI: re-check SHIPPED citations in out/**/*.v2.json against the real
                   PDFs. Read-only, ZERO model calls (both asserted statically by its smoke test)
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
- **Marks are a BOARD fact, not a model judgement:** `marks_convention_for(level)`
  (sibling of `exam_tips_for`) is the single source of truth for whether a level
  mark-weights at all, what its mark scheme is CALLED, and how its steps are labelled —
  read by the practice prompt, the deterministic gate AND the renderer, so the
  instruction, the check and the student-facing label cannot disagree. It exists because
  SAT numerics correctly carried `marks: null` and then labelled their schemes `M1`/`A1`
  — Edexcel method marks on a College Board product — under a hardcoded "Mark scheme"
  heading. SAT/AMC are paced by `time_estimate_s` instead. The JS `SCHEME_TITLE` map
  duplicates the no-marks levels, so `_smoke_v2.py` pins that parity exactly as it pins
  `BLOCK_TYPES` ↔ `renderBlock`.

## Tracing doctrine — every model call is traced, named, and tagged

**Not a single model call goes out untraced.** A call that isn't traced is spend nobody
can attribute; a call traced as "unknown" is barely better. This is enforced structurally,
not by convention (`tests/_smoke_tracing.py` asserts every clause):

- **One door.** `helpers.call_model` is the ONLY place that calls `generate_content` — so
  the trace requirement cannot be routed around.
- **`trace` is required.** It is keyword-only with **no default**, built by
  `helpers.trace_topic` / `trace_spec_run` / `trace_for`. A new call site that forgets it
  fails at the call, not silently in Langfuse. `trace_for` rejects an undeclared
  feature/stage, so a typo fails loudly in dev instead of landing as an anonymous call.
  It also supplies the console label — logs and Langfuse cannot drift apart.
- **Names are the aggregation axis, so keep them low-cardinality.** `feature` names the
  TRACE (`notes.generate`, `notes.extract_specs`, `notes.ground_specs` — a new model-calling
  CLI adds itself to `helpers.FEATURES`); `stage` names the OBSERVATION as `notes.<stage>`
  (`notes.section`, `notes.coverage` — declared in `helpers.STAGES`, kept in lockstep with
  the call sites exactly like `BLOCK_TYPES` ↔ `renderBlock`). The variable part (topic,
  heading) goes in **tags/metadata, never the name** — names like `v2-section:Periodic
  Trends and Co` mint one name per section and aggregate to nothing.
- **Tags are the filter axis:** `app:class-notes`, `feature:`, `stage:`, `board:`,
  `subject:`, `level:`, `topic:`. Langfuse silently DROPS a propagated value that isn't a
  `str` or exceeds 200 chars, so `trace_for` coerces + clips — a quietly missing tag is the
  same blind spot as no tag.
- **`group` is the unit of work** one trace covers and seeds a **deterministic trace id**:
  every stage of one topic (outline → section → coverage → images → practice → finalize →
  papers) rolls up under ONE trace and one cost total. Seeded, never OTel context — the
  pipeline fans out across threads (`--jobs` × the section pool) and contextvars do **not**
  cross a `ThreadPoolExecutor` boundary. Anything needing the topic's trace must take it as
  a plain argument (this is why `fetch_images_for_blocks` takes `spec`).
- **The contract is validated; the transmission is best-effort.** A dropped cost record must
  never cost a topic, so `_log_generation` swallows everything. The contract itself is pure +
  deterministic, so the smoke test exercises it with no key.
- **A traced call is only a COSTED call if every billable token bucket is sent.** Gemini splits
  usage FOUR ways and `candidates_token_count` is only the VISIBLE answer — the reasoning
  tokens land in a separate `thoughts_token_count` that is billed at the OUTPUT rate. Sending
  just prompt+candidates (as this did through the first pilot) under-reports the real bill by
  ~62%: measured live, thinking runs 185–211% of visible output, so true cost was **2.6x**
  what Langfuse showed. `helpers._usage_details` maps all four; the invariant is
  **`sum(usage.values()) == total_token_count`**, and anything left over surfaces as a loud
  one-time residual warning rather than vanishing. Two traps it encodes: Langfuse matches each
  usage key EXACTLY against the model definition's price keys (an unmatched name silently
  costs $0), and it buckets a key as input/output by whether the NAME CONTAINS "input"/"output"
  — hence `output_reasoning_tokens` (priced identically to `thoughts_token_count`, but it also
  makes Langfuse's output-token totals reconcile against Vertex). Cached tokens sit INSIDE
  `prompt_token_count` but bill at ~10%, so `input` carries only the uncached remainder —
  usage types must be mutually exclusive buckets. Never send `total`: Langfuse derives it, and
  a model definition that prices it would double-charge every call.
- **No keys ⇒ no record, and that must be audible.** Langfuse is optional, so a `.env` without
  `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` spends real money with nothing written anywhere — the same
  blind spot as an untraced call, so the OFF path now says so on every run. The dev `.env`s
  carry only Vertex creds (the keys live on the server), so **local runs are unrecorded by
  default**; `notes.ground_specs` currently has zero observations for that reason, while the
  pre-refactor `spec-ground:<topic>` traces from the same CLI are still in the data.
- **Reconciling against Vertex: Langfuse is not the whole bill.** The Vertex project
  (`gen-lang-client-0547340259`) is SHARED — the Grader uses the same service account and has no
  Langfuse integration at all, and several other AP Guru surfaces (`error_analysis_run`,
  `weekly_plan_run`, `grader.grade`, `gemini.generate_structured`) write into the same Langfuse
  project. So compare like for like: filter Langfuse on `app:class-notes` (only this repo sets
  it), and expect the GCP bill to exceed it by whatever the co-tenants spend.
- **SDK trap (v4):** `propagate_attributes(trace_name=...)` is the ONLY API that names a
  trace — v3's `update_current_trace` / `span.update_trace` are **gone**, and a
  `trace_context` trace with no name renders as **"unknown"** (this cost the project ~$24
  of unattributable spend before it was fixed). It is contextvar-scoped, so it MUST wrap
  `start_observation` **in the same thread** — never build it on a parent thread.

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
  identity fields (board/level/id) are stamped deterministically not model-guessed, and
  every spec must clear the **curriculum gate** before it can generate.
  **`UNVERIFIED` now means "the curriculum gate could not verify this against its source
  PDF", not "no human has looked."** It is set by `extract_specs.stamp_extracted` (a caller
  that has not run the gate gets it by default — an approval must be earned) and cleared
  ONLY when, in one run, every code AND every objective's `evidence_quote` was located in
  the PDF pages actually sent. It stays a hard **generation gate**: `notes.py` skips it
  (`batch.is_unverified`), single-topic runs no longer auto-override it, and
  `--include-unverified` now overrides a DETERMINISTIC check rather than the absence of a
  human one — so it stamps a `SPEC UNGROUNDED` entry into `review_flags` for the spot-check
  queue. `batch.set_unverified_reason` makes the marker self-describing, and cannot clear
  the gate. `_smoke_extract_specs.py` asserts the stamp and the gate token stay in sync, and
  that the stamp never instructs a human.
- **Separation of powers, without a human.** The old gate's value was never "a person
  looked" — it was that the thing PRODUCING a claim is not the thing ACCEPTING it. That
  survives: the producer is a model, the acceptor (`spec_gate.py`) is a pure genai-free
  function over fetched bytes that no model reasoning can influence. This is why the writer
  never approves itself and why the whole trust boundary is offline-testable.
- **Know which half of the evidence is load-bearing.** Measured on the real 238-page AP
  Chemistry CED: the code check discriminates well for STRUCTURED codes (28/28 real ones
  found; `SAP-9.Z`, `TRA-99.A` correctly absent) but a BARE NUMERIC code collides by chance
  — `1.1` folds to `11`, found; so do `9.9` and `12.4`. Edexcel (`8.1`) and IGCSE (`6.7S`)
  print exactly that. So the **evidence quote carries the proof** (40 contiguous verbatim
  chars cannot collide); the code is a cheap filter. Do not "fix" this by demanding longer
  codes — it would mark every Edexcel/IGCSE code absent and block those boards.
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
