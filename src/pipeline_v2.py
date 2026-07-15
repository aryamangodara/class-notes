"""Interactive-notes generation pipeline (the sole notes-generation pipeline).

Reuses helpers' client/retry (`call_model`), config wiring (`_gen_config`),
grounding (`_spec_block`), the outline stage, and the image-fetch internals.
Produces an `InteractiveNotes`: typed interactive blocks per section + a practice
ladder + the curated exam-format layer passed through from the TopicSpec.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Annotated, Union

from google import genai
from pydantic import BaseModel, Field

import helpers
import schemas_v2 as v2
from config import CONFIG, HOUSE_STYLE, exam_tips_for
from render_v2 import block_defects, practice_block_defects, section_block_defects
from schemas import CoverageReport, TopicSpec
from coverage_gate import (
    CoverageError, StructuralError, defect_feedback_by_section, feedback_block,
    plan_regeneration, structural_feedback_block, structural_feedback_lines,
    structural_gap_items, uncovered_items,
)

# The practice ladder is mcq | numeric only (plain Union -> anyOf, no discriminator).
_PracticeBlock = Union[v2.MCQBlock, v2.NumericBlock]


class _PracticeSet(BaseModel):
    questions: list[_PracticeBlock] = Field(
        description="FIVE or SIX questions forming a basic->stretch ladder (mcq or numeric).")


class _Finalize(BaseModel):
    hero: v2.Hero
    hook: v2.RevealBlock
    command_words: list[v2.CommandWord] = Field(default_factory=list)
    mistakes: list[v2.AccordionItem] = Field(default_factory=list)
    checklist_recaps: list[str] = Field(
        default_factory=list,
        description="One short recap string per spec-checklist item, in the SAME ORDER as the "
        "checklist statements given; '' where no recap helps.")


# ---------------------------------------------------------------------------
# block-text flattening (for practice context + coverage verify)
# ---------------------------------------------------------------------------

def _block_text(b) -> str:
    t = b.type
    if t == "prose":
        return b.body
    if t == "callout":
        return f"{b.title} {b.body}"
    if t == "table":
        return " ".join(b.headers) + " " + " ".join(c for r in b.rows for c in r)
    if t == "flip_cards":
        return " ".join(f"{c.front}: {c.back}" for c in b.cards)
    if t == "mcq":
        return b.question + " " + " ".join(o.text for o in b.options)
    if t == "step_reveal":
        return b.prompt + " " + " ".join(f"{s.title} {s.body} {s.formula}" for s in b.steps)
    if t == "numeric":
        return b.question + " " + " ".join(m.text for m in b.mark_scheme)
    if t == "sort":
        return b.prompt
    if t in ("toggle_diagram", "cycle_diagram"):
        return getattr(b, "caption", "") or getattr(b, "title", "")
    if t == "accordion":
        return " ".join(f"{i.summary} {i.detail}" for i in b.items)
    return ""


def _section_text(sec: v2.InteractiveSection) -> str:
    return f"## {sec.heading}\n" + "\n".join(_block_text(b) for b in sec.blocks)


def _worked_examples_text(sections: list[v2.InteractiveSection]) -> str:
    out = []
    for s in sections:
        for b in s.blocks:
            if b.type == "step_reveal":
                out.append(f"[{s.heading}] {b.prompt}\n" + "\n".join(
                    f"{st.title} {st.body} {st.formula}" for st in b.steps))
    return "\n\n".join(out) or "(none)"


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def write_section_v2(client: genai.Client, spec: TopicSpec, section, outline,
                     coverage_feedback: str = "") -> v2.InteractiveSection:
    others = "\n".join(
        f"  - {s.heading}: {s.intent} [{', '.join(s.covers_objective_codes) or 'none'}]"
        for s in outline.sections if s is not section) or "  (this is the only section)"
    exam_format = "\n".join(f"  - {t}" for t in exam_tips_for(spec.level, spec.subject)) or "  (none)"
    prompt = helpers.load_prompt("v2_write_section.txt").format(
        house_style=HOUSE_STYLE, spec_block=helpers._spec_block(spec),
        heading=section.heading, intent=section.intent,
        codes=", ".join(section.covers_objective_codes) or "(none specified)",
        outline=others, exam_format=exam_format,
        coverage_feedback=coverage_feedback,
    )
    return helpers.call_model(
        client, label=f"v2-section:{section.heading[:22]}", contents=prompt,
        **helpers._gen_config("model_write", "temperature_write", v2.InteractiveSection))


def write_sections_v2(client: genai.Client, spec: TopicSpec, outline) -> list[v2.InteractiveSection]:
    results: list = [None] * len(outline.sections)
    with ThreadPoolExecutor(max_workers=CONFIG["max_parallel_sections"]) as ex:
        futs = {ex.submit(write_section_v2, client, spec, s, outline): i
                for i, s in enumerate(outline.sections)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
    return [r for r in results if r is not None]


def write_practice_v2(client: genai.Client, spec: TopicSpec, sections,
                      structural_feedback: str = "") -> list:
    joined = "\n\n".join(_section_text(s) for s in sections)
    prompt = helpers.load_prompt("v2_write_practice.txt").format(
        house_style=HOUSE_STYLE, spec_block=helpers._spec_block(spec),
        sections=joined, worked_examples=_worked_examples_text(sections),
        structural_feedback=structural_feedback)
    ps = helpers.call_model(
        client, label=f"v2-practice:{spec.topic_id}", contents=prompt,
        **helpers._gen_config("model_write", "temperature_write", _PracticeSet))
    return list(ps.questions)


def enforce_practice_structure_v2(client: genai.Client, spec: TopicSpec, sections, practice) -> list:
    """The practice-ladder structural GATE (sibling of the section gate). Runs the
    deterministic block-completeness check on the generated ladder — every MCQ option
    explained, every numeric with a mark scheme, a positive tolerance and no diagnostic
    within tolerance of the answer — and regenerates the WHOLE ladder with the defects
    injected, up to ``max_structure_retries``, then hard-fails (``StructuralError``). A
    broken practice question (the reviewer's 'missing answer key') never ships.
    """
    max_retries = CONFIG.get("max_structure_retries", 1)
    attempt = 0
    while True:
        defects = practice_block_defects(practice)
        if not defects:
            return practice
        if attempt >= max_retries:
            raise StructuralError(spec.topic_id, defects)
        attempt += 1
        print(f"       practice gate: {len(defects)} block defect(s) — "
              f"regenerating the ladder ({attempt}/{max_retries})")
        practice = write_practice_v2(
            client, spec, sections,
            structural_feedback=structural_feedback_block(structural_feedback_lines(defects)))


def finalize_v2(client: genai.Client, spec: TopicSpec, sections) -> _Finalize:
    joined = "\n\n".join(_section_text(s) for s in sections)
    checklist = "\n".join(f"  {i+1}. [{it.code}] {it.can_do}"
                          for i, it in enumerate(spec.spec_checklist)) or "  (none)"
    prompt = helpers.load_prompt("v2_finalize.txt").format(
        house_style=HOUSE_STYLE, spec_block=helpers._spec_block(spec),
        sections=joined, checklist=checklist)
    return helpers.call_model(
        client, label=f"v2-finalize:{spec.topic_id}", contents=prompt,
        **helpers._gen_config("model_write", "temperature_write", _Finalize))


def _fetch_one_image(client: genai.Client, d, width: int):
    """Search + download + vision-select for ONE figure slot. Returns the chosen
    candidate dict (with `_bytes`) or None. Independent per slot, so this is the unit
    the image stage fans out across figures — same searches/vision as before."""
    query = (d.content or d.caption or "").strip()
    if not query:
        return None
    cands = helpers._search_images(query, n=8, width=width)[:6]
    for c in cands:
        try:
            c["_bytes"] = helpers._http_get(c["thumb"])
        except Exception:
            c["_bytes"] = b""
    cands = [c for c in cands if c.get("_bytes")]
    if not cands:
        print(f"    image: nothing usable for '{query[:40]}'")
        return None
    idx = helpers._select_image(client, query, d.caption, cands) if CONFIG.get("image_vision_select") else 0
    if idx < 0:
        print(f"    image: no suitable match for '{query[:40]}'")
        return None
    return cands[idx]


def fetch_images_for_blocks(client: genai.Client, sections, *, max_images: int, width: int) -> int:
    """Search/select/embed images for `figure` blocks (kind='image') in place.

    The per-figure search+vision is fanned out across slots (each independent), then the
    successful picks are embedded IN SLOT ORDER up to ``max_images`` — the exact same
    selection the old sequential loop made, only concurrent.
    """
    slots = [b.diagram for s in sections for b in s.blocks
             if b.type == "figure" and b.diagram.kind == "image" and not b.diagram.image_src]
    if not slots:
        return 0
    picks: list = [None] * len(slots)
    workers = min(len(slots), CONFIG.get("max_parallel_sections", 4))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_one_image, client, d, width): i for i, d in enumerate(slots)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                picks[i] = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"    image fetch failed: {exc}")
                picks[i] = None
    embedded = 0
    for d, c in zip(slots, picks):
        if embedded >= max_images:
            break
        if not c:
            continue
        d.image_src = helpers._data_uri(c["_bytes"], c["mime"])
        d.attribution = helpers._attribution(c)
        embedded += 1
        print(f"    image: '{(d.content or d.caption)[:38]}' -> {c['title'][:36]} ({c['license']})")
    return embedded


def verify_v2(client: genai.Client, spec: TopicSpec, sections) -> CoverageReport:
    joined = "\n\n".join(_section_text(s) for s in sections)
    prompt = helpers.load_prompt("verify.txt").format(spec_block=helpers._spec_block(spec), notes=joined)
    # `model_coverage` (writer-strength by default): this audit GATES the build, so
    # it must not be a weak rubber-stamp. It sees only the notes + contract, never
    # the writer's reasoning, so it stays an independent second read of the output.
    return helpers.call_model(
        client, label=f"v2-verify:{spec.topic_id}", contents=prompt,
        **helpers._gen_config("model_coverage", "temperature_coverage", CoverageReport))


def enforce_coverage_v2(client: genai.Client, spec: TopicSpec, outline, sections) -> CoverageReport:
    """The section GATE: verify coverage AND deterministic block completeness, then
    targeted re-draft of the section(s) that own an uncovered objective OR a broken
    block (the relevant feedback injected), then re-verify — up to
    ``max_coverage_retries``. Sections are mutated in place. Raises ``CoverageError``
    (a contract gap) or ``StructuralError`` (a broken block) if the fault survives, so
    a section that cannot be made BOTH covered and structurally complete NEVER produces
    an artifact. Both faults are FACTS the pipeline fixes-or-fails on — neither slips
    through as a soft flag (the model verifier's opinions do, routed to spot-check).
    """
    max_retries = CONFIG.get("max_coverage_retries", 2)
    coverage = verify_v2(client, spec, sections)
    attempt = 0
    while True:
        model_gaps = uncovered_items(coverage.items)
        # Deterministic structural evidence: an objective whose command word demands
        # a specific artifact (prove -> step_reveal, calculate -> numeric/sim) is a
        # gap when that artifact is absent, EVEN IF the model marked it covered.
        struct_gaps = structural_gap_items(
            spec.learning_objectives, sections,
            include_recall=CONFIG.get("structural_gate_recall", False))
        seen = {c.code for c in model_gaps}
        gaps = model_gaps + [g for g in struct_gaps if g.code not in seen]
        # Deterministic per-block completeness (an unexplained MCQ option, a numeric
        # with no mark scheme, an empty worked example, a dead widget). These are
        # FACTS, so they gate exactly like a coverage gap — re-drafting the owning
        # section here (before images/practice) also keeps them coverage-safe: every
        # regeneration is re-verified in this same loop.
        sec_defects = section_block_defects(sections)
        defect_targets = defect_feedback_by_section(sec_defects)

        if not gaps and not defect_targets:
            return coverage
        if attempt >= max_retries:
            if gaps:
                raise CoverageError(spec.topic_id, gaps)
            raise StructuralError(spec.topic_id, sec_defects)
        attempt += 1
        reasons = []
        if gaps:
            reasons.append("coverage " + ", ".join(c.code for c in gaps))
        if defect_targets:
            reasons.append(f"structure {sum(len(v) for v in defect_targets.values())} defect(s)")
        print(f"       section gate [{' | '.join(reasons)}] — "
              f"regenerating owning section(s) ({attempt}/{max_retries})")
        texts = [_section_text(s) for s in sections]
        targets, forced = plan_regeneration(gaps, sections, texts, spec.learning_objectives)
        # Route codes no section claimed onto their best-overlap section, so the
        # re-draft is told to teach them and the next verify can find them.
        for i, codes in forced.items():
            outline.sections[i].covers_objective_codes = list(
                dict.fromkeys([*outline.sections[i].covers_objective_codes, *codes]))
            sections[i].covers_objective_codes = list(
                dict.fromkeys([*sections[i].covers_objective_codes, *codes]))

        redraw = set(targets) | set(defect_targets)

        def _redraw(i: int):
            feedback = feedback_block(targets.get(i, [])) + structural_feedback_block(defect_targets.get(i, []))
            return i, write_section_v2(client, spec, outline.sections[i], outline,
                                       coverage_feedback=feedback)

        with ThreadPoolExecutor(max_workers=CONFIG["max_parallel_sections"]) as ex:
            for fut in as_completed([ex.submit(_redraw, i) for i in redraw]):
                i, sec = fut.result()
                sections[i] = sec
        coverage = verify_v2(client, spec, sections)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def generate_interactive_notes(client: genai.Client, spec: TopicSpec) -> v2.InteractiveNotes:
    print(f"[1/5] outline    {spec.topic_id}")
    outline = helpers.generate_outline(client, spec)
    print(f"      planned {len(outline.sections)} section(s)")

    print(f"[2/5] blocks     {len(outline.sections)} section(s) in parallel")
    sections = write_sections_v2(client, spec, outline)

    # Coverage GATE — settle sections against the contract BEFORE spending
    # image/practice/finalize calls on them. Hard-fails (raises CoverageError) if a
    # gap cannot be closed, so nothing under-covered is ever assembled or written.
    print(f"[3/5] coverage   enforcing coverage of {len(spec.learning_objectives)} objective(s)")
    coverage = enforce_coverage_v2(client, spec, outline, sections)

    # Post-coverage stages — images, practice, finalize, past-papers — depend only on the
    # now-settled sections/spec and are mutually independent (images touch only figure
    # blocks; _block_text ignores figures, so practice/finalize never read what images
    # writes). Run them CONCURRENTLY, bounded globally by the call_model governor. Same
    # models/stages/gates as before — only overlapped — so output is unaffected and
    # wall-clock shrinks. Images & past-papers never fail a topic; practice & finalize
    # propagate (a broken ladder must still hard-fail via the structural gate).
    def _stage_images():
        if not CONFIG.get("image_search"):
            return
        try:
            k = fetch_images_for_blocks(client, sections,
                                        max_images=CONFIG["max_images_per_topic"], width=CONFIG["image_width"])
            print(f"       embedded {k} image(s)")
        except Exception as exc:  # noqa: BLE001 — images never fail a topic
            print(f"       image stage skipped: {exc}")

    def _stage_practice():
        p = write_practice_v2(client, spec, sections)
        return enforce_practice_structure_v2(client, spec, sections, p)

    def _stage_past_papers():
        pp = spec.past_papers
        if CONFIG.get("generate_past_papers") and not (pp and pp.verified):
            try:
                from past_papers import build_past_papers
                return build_past_papers(client, spec) or pp
            except Exception as exc:  # noqa: BLE001 — never let the paper stage fail a topic
                print(f"       past-paper stage skipped: {exc}")
        return pp

    print("[4/5] stages     images | practice | finalize | past-papers (parallel)")
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_img = ex.submit(_stage_images)
        f_practice = ex.submit(_stage_practice)
        f_finalize = ex.submit(finalize_v2, client, spec, sections)
        f_pp = ex.submit(_stage_past_papers)
        f_img.result()                    # images mutate sections in place; just join
        practice = f_practice.result()    # propagates StructuralError/RuntimeError -> topic fails
        fin = f_finalize.result()         # propagates
        past_papers = f_pp.result()

    print("[5/5] assemble   interactive notes")
    # curated passthrough (never generated)
    exam_map = v2.ExamMap(cells=spec.exam_map) if spec.exam_map else None
    checklist = None
    if spec.spec_checklist:
        recaps = fin.checklist_recaps + [""] * len(spec.spec_checklist)
        checklist = v2.SpecChecklist(
            source_title=f"{spec.board} · {spec.unit}",
            source_citation=spec.spec_source_citation,
            items=[v2.SpecChecklistItem(code=it.code, can_do=it.can_do, recap=recaps[i])
                   for i, it in enumerate(spec.spec_checklist)])

    notes = v2.InteractiveNotes(
        topic_id=spec.topic_id, board=spec.board, subject=spec.subject, level=spec.level,
        unit=spec.unit, topic=spec.topic, learning_objectives=spec.learning_objectives,
        hero=fin.hero, exam_map=exam_map, hook=fin.hook, sections=sections, practice=practice,
        command_words=fin.command_words, mistakes=fin.mistakes, spec_checklist=checklist,
        past_papers=past_papers, finish=v2.Finish(next_topic=spec.next_topic),
        coverage_report=coverage.items,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    # Final structural guard: the section + practice gates have already fixed-or-failed
    # every deterministic block defect, so this must come back clean. If ANY survives
    # (e.g. a future checked block type on a locus no gate covers), refuse to ship
    # rather than emit a broken interactive — the same fix-or-fail contract as coverage.
    residual = block_defects(notes)
    if residual:
        raise StructuralError(spec.topic_id, residual)

    # review_flags now carry ONLY the model verifier's ADVISORY opinions. A single
    # model read can be wrong (it can flag a complete block as incomplete — exactly the
    # false positive that would make a blanket "block on any flag" rule regenerate good
    # work), so these are routed to the human spot-check queue, NOT hard-blocked. Every
    # deterministic FACT has already been fixed-or-failed by the gates above.
    notes.review_flags = list(coverage.review_flags)
    covered = sum(1 for c in coverage.items if c.covered)
    print(f"  ok {covered}/{len(coverage.items)} objectives covered | {len(sections)} sections | "
          f"{len(practice)} practice | {len(notes.review_flags)} advisory flag(s)")
    return notes


# Characters Windows/most filesystems reject in a path segment. Board/subject names
# may contain spaces and parentheses ("AP (College Board)") — those are fine to keep.
_FS_ILLEGAL = '<>:"/\\|?*'


def _fs_safe(name: str) -> str:
    """One human-readable, filesystem-safe path segment from a board/subject name:
    drop illegal characters, collapse whitespace, trim trailing dots/spaces."""
    cleaned = "".join(" " if c in _FS_ILLEGAL else c for c in (name or ""))
    return " ".join(cleaned.split()).rstrip(". ") or "unknown"


def save_interactive_notes(n: v2.InteractiveNotes, out_dir: str | None = None) -> None:
    from pathlib import Path
    from render_v2 import render_interactive_html
    # Group on disk by board then subject:
    #   out/<board>/<subject>/<topic_id>.{v2.json,interactive.html}
    d = Path(out_dir or CONFIG["out_dir"]) / _fs_safe(n.board) / _fs_safe(n.subject)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n.topic_id}.v2.json").write_text(n.model_dump_json(indent=2), encoding="utf-8")
    (d / f"{n.topic_id}.interactive.html").write_text(render_interactive_html(n), encoding="utf-8")
    print(f"    {d / (n.topic_id + '.v2.json')}")
    print(f"    {d / (n.topic_id + '.interactive.html')}")
