"""v2 interactive-notes generation pipeline (lives alongside v1 generate_notes).

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
from config import BOARD_EXAM_TIPS, CONFIG, HOUSE_STYLE
from render_v2 import validate_interactives
from schemas import CoverageReport, TopicSpec

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

def write_section_v2(client: genai.Client, spec: TopicSpec, section, outline) -> v2.InteractiveSection:
    others = "\n".join(
        f"  - {s.heading}: {s.intent} [{', '.join(s.covers_objective_codes) or 'none'}]"
        for s in outline.sections if s is not section) or "  (this is the only section)"
    exam_format = "\n".join(f"  - {t}" for t in BOARD_EXAM_TIPS.get(spec.level, [])) or "  (none)"
    prompt = helpers.load_prompt("v2_write_section.txt").format(
        house_style=HOUSE_STYLE, spec_block=helpers._spec_block(spec),
        heading=section.heading, intent=section.intent,
        codes=", ".join(section.covers_objective_codes) or "(none specified)",
        outline=others, exam_format=exam_format,
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


def write_practice_v2(client: genai.Client, spec: TopicSpec, sections) -> list:
    joined = "\n\n".join(_section_text(s) for s in sections)
    prompt = helpers.load_prompt("v2_write_practice.txt").format(
        house_style=HOUSE_STYLE, spec_block=helpers._spec_block(spec),
        sections=joined, worked_examples=_worked_examples_text(sections))
    ps = helpers.call_model(
        client, label=f"v2-practice:{spec.topic_id}", contents=prompt,
        **helpers._gen_config("model_write", "temperature_write", _PracticeSet))
    return list(ps.questions)


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


def fetch_images_for_blocks(client: genai.Client, sections, *, max_images: int, width: int) -> int:
    """Search/select/embed images for `figure` blocks (kind='image') in place."""
    slots = [b.diagram for s in sections for b in s.blocks
             if b.type == "figure" and b.diagram.kind == "image" and not b.diagram.image_src]
    embedded = 0
    for d in slots:
        if embedded >= max_images:
            break
        query = (d.content or d.caption or "").strip()
        if not query:
            continue
        try:
            cands = helpers._search_images(query, n=8, width=width)[:6]
            for c in cands:
                try:
                    c["_bytes"] = helpers._http_get(c["thumb"])
                except Exception:
                    c["_bytes"] = b""
            cands = [c for c in cands if c.get("_bytes")]
            if not cands:
                print(f"    image: nothing usable for '{query[:40]}'")
                continue
            idx = helpers._select_image(client, query, d.caption, cands) if CONFIG.get("image_vision_select") else 0
            if idx < 0:
                print(f"    image: no suitable match for '{query[:40]}'")
                continue
            c = cands[idx]
            d.image_src = helpers._data_uri(c["_bytes"], c["mime"])
            d.attribution = helpers._attribution(c)
            embedded += 1
            print(f"    image: '{query[:38]}' -> {c['title'][:36]} ({c['license']})")
        except Exception as exc:  # noqa: BLE001
            print(f"    image fetch failed for '{query[:40]}': {exc}")
    return embedded


def verify_v2(client: genai.Client, spec: TopicSpec, sections) -> CoverageReport:
    joined = "\n\n".join(_section_text(s) for s in sections)
    prompt = helpers.load_prompt("verify.txt").format(spec_block=helpers._spec_block(spec), notes=joined)
    return helpers.call_model(
        client, label=f"v2-verify:{spec.topic_id}", contents=prompt,
        **helpers._gen_config("model_verify", "temperature_verify", CoverageReport))


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def generate_interactive_notes(client: genai.Client, spec: TopicSpec) -> v2.InteractiveNotes:
    print(f"[1/5] outline    {spec.topic_id}")
    outline = helpers.generate_outline(client, spec)
    print(f"      planned {len(outline.sections)} section(s)")

    print(f"[2/5] blocks     {len(outline.sections)} section(s) in parallel")
    sections = write_sections_v2(client, spec, outline)

    if CONFIG.get("image_search"):
        print("[img]  fetching images for figure blocks")
        try:
            k = fetch_images_for_blocks(client, sections,
                                        max_images=CONFIG["max_images_per_topic"], width=CONFIG["image_width"])
            print(f"       embedded {k} image(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"       image stage skipped: {exc}")

    print("[3/5] practice   5-6 question ladder")
    practice = write_practice_v2(client, spec, sections)

    print("[4/5] finalize   hero, hook, command words, mistakes, recaps")
    fin = finalize_v2(client, spec, sections)

    print(f"[5/5] verify     coverage of {len(spec.learning_objectives)} objective(s)")
    coverage = verify_v2(client, spec, sections)

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
        past_papers=spec.past_papers, finish=v2.Finish(next_topic=spec.next_topic),
        coverage_report=coverage.items,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    # review flags: verifier flags + uncovered + deterministic interactive validation
    flags = list(coverage.review_flags)
    uncovered = [c.code for c in coverage.items if not c.covered]
    if uncovered:
        flags.append(f"Objectives not fully covered: {', '.join(uncovered)}")
    flags.extend(validate_interactives(notes))
    notes.review_flags = flags
    covered = sum(1 for c in coverage.items if c.covered)
    print(f"  ok {covered}/{len(coverage.items)} objectives | {len(sections)} sections | "
          f"{len(practice)} practice | {len(flags)} flag(s)")
    return notes


def save_interactive_notes(n: v2.InteractiveNotes, out_dir: str | None = None) -> None:
    from pathlib import Path
    from render_v2 import render_interactive_html
    d = Path(out_dir or CONFIG["out_dir"])
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n.topic_id}.v2.json").write_text(n.model_dump_json(indent=2), encoding="utf-8")
    (d / f"{n.topic_id}.interactive.html").write_text(render_interactive_html(n), encoding="utf-8")
    print(f"    {d / (n.topic_id + '.v2.json')}")
    print(f"    {d / (n.topic_id + '.interactive.html')}")
