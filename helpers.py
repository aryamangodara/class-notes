"""Core logic for the Class Notes generator.

Thin CLI/notebook orchestration sits on top of this; all real work lives here so
the entry point stays readable (same split as the Grader).

The Gemini client and retry wrapper are mirrored from ``Grader/helpers.py`` so
this POC is plug-compatible with the existing stack: same auth, same models,
same structured-output convention (Pydantic ``response_schema`` -> ``response.parsed``).

Pipeline (per topic):
    1. outline        plan sections that cover every learning objective
    2. write_sections draft each section, grounded + parallel
    3. finalize       assemble key terms, misconceptions, exam tips, practice, summary
    4. verify         audit coverage of every objective; collect review flags
    5. render/save    md + self-contained html + json
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from config import CONFIG, HOUSE_STYLE
from schemas import (
    ClassNotes,
    CoverageReport,
    ImageChoice,
    NoteSection,
    NotesExtras,
    NotesOutline,
    TopicSpec,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# ---------------------------------------------------------------------------
# Gemini client + retry (mirrored from Grader/helpers.py)
# ---------------------------------------------------------------------------

def get_gemini_client(timeout_ms: int = 300_000) -> genai.Client:
    """Prefer GEMINI_API_KEY (AI Studio); fall back to Vertex AI.

    Identical contract to Grader/helpers.py, so the same ``.env`` works here.
    """
    http_options = types.HttpOptions(timeout=timeout_ms)
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key, http_options=http_options)

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS is set but GOOGLE_CLOUD_PROJECT is not. "
                "Add GOOGLE_CLOUD_PROJECT=<your-gcp-project-id> to .env."
            )
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        return genai.Client(
            vertexai=True, project=project, location=location, http_options=http_options
        )

    raise RuntimeError(
        "No Gemini credentials found. In .env set either:\n"
        "  GEMINI_API_KEY=...                                  (AI Studio, simpler)\n"
        "or:\n"
        "  GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/sa.json   (Vertex AI)\n"
        "  GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>\n"
    )


_RETRYABLE = {408, 429, 499, 500, 502, 503, 504}
_RETRYABLE_WORDS = ("UNAVAILABLE", "DEADLINE", "RESOURCE_EXHAUSTED", "INTERNAL", "CANCELLED", "ABORTED")


def _transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    blob = str(exc).upper()
    if isinstance(code, int) and code in _RETRYABLE:
        return True
    return any(w in blob for w in _RETRYABLE_WORDS) or any(str(c) in blob for c in _RETRYABLE)


def call_model(client: genai.Client, *, label: str = "", max_attempts: int = 4,
               base_delay: float = 2.0, **kwargs):
    """Call ``client.models.generate_content`` with retry, returning the parsed
    Pydantic object (``response.parsed``).

    Retries transient API errors (429/499/5xx, CANCELLED/UNAVAILABLE/...) and
    empty structured responses (``parsed is None`` — usually a safety filter or
    a MAX_TOKENS truncation) with exponential backoff + jitter. Non-transient
    errors (400, auth) raise immediately.
    """
    tag = f" [{label}]" if label else ""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.models.generate_content(**kwargs)
        except Exception as exc:  # noqa: BLE001 — classify then re-raise
            if attempt == max_attempts or not _transient(exc):
                raise
            delay = base_delay * 2 ** (attempt - 1) + random.uniform(0, 1)
            print(f"    transient error{tag} (attempt {attempt}/{max_attempts}): {exc}; retrying in {delay:.1f}s")
            time.sleep(delay)
            continue

        parsed = getattr(resp, "parsed", None)
        if parsed is None:
            if attempt == max_attempts:
                snippet = (getattr(resp, "text", None) or "")[:200]
                raise RuntimeError(f"Empty Gemini response{tag}: {snippet!r}")
            delay = base_delay * 2 ** (attempt - 1) + random.uniform(0, 1)
            print(f"    empty response{tag} (attempt {attempt}/{max_attempts}); retrying in {delay:.1f}s")
            time.sleep(delay)
            continue
        return parsed
    raise RuntimeError("unreachable")


def _gen_config(model_key: str, temp_key: str, schema) -> dict:
    """Build the kwargs for a structured-output call (model + GenerateContentConfig)."""
    return dict(
        model=CONFIG[model_key],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=CONFIG[temp_key],
        ),
    )


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Curriculum (grounding)
# ---------------------------------------------------------------------------

def discover_topics() -> dict[str, TopicSpec]:
    """Load every TopicSpec under curriculum/. Drop a JSON in, it's picked up."""
    d = Path(CONFIG["curriculum_dir"])
    topics: dict[str, TopicSpec] = {}
    for f in sorted(d.glob("*.json")):
        spec = TopicSpec.model_validate_json(f.read_text(encoding="utf-8"))
        topics[spec.topic_id] = spec
    return topics


def load_topic_spec(topic_id: str) -> TopicSpec:
    topics = discover_topics()
    if topic_id not in topics:
        raise KeyError(f"Unknown topic '{topic_id}'. Available: {', '.join(topics) or '(none)'}")
    return topics[topic_id]


def _spec_block(spec: TopicSpec) -> str:
    """The grounding context injected into every stage's prompt."""
    los = "\n".join(
        f"  - [{lo.code}] {lo.statement}"
        + (f"  (tier: {lo.tier})" if lo.tier else "")
        + (f"  (command words: {', '.join(lo.command_words)})" if lo.command_words else "")
        for lo in spec.learning_objectives
    )
    prereqs = ", ".join(spec.prerequisites) or "none stated"
    return (
        f"BOARD: {spec.board}\nSUBJECT: {spec.subject}\nLEVEL: {spec.level}\n"
        f"UNIT: {spec.unit}\nTOPIC: {spec.topic}\n"
        f"PREREQUISITES (assume known, do not re-teach): {prereqs}\n\n"
        f"DEPTH PROFILE (calibrate exactly to this):\n{spec.depth_profile}\n\n"
        f"ASSESSMENT NOTES (teach toward this):\n{spec.assessment_notes}\n\n"
        f"LEARNING OBJECTIVES (the contract — cover all, exceed none):\n{los}\n"
    )


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def generate_outline(client: genai.Client, spec: TopicSpec) -> NotesOutline:
    prompt = load_prompt("outline.txt").format(house_style=HOUSE_STYLE, spec_block=_spec_block(spec))
    return call_model(client, label=f"outline:{spec.topic_id}", contents=prompt,
                      **_gen_config("model_plan", "temperature_plan", NotesOutline))


def write_section(client: genai.Client, spec: TopicSpec, section,
                  outline: NotesOutline) -> NoteSection:
    # Show this section its siblings so parallel drafts don't re-teach each
    # other's material (each objective is owned by one primary section).
    others = "\n".join(
        f"  - {s.heading}: {s.intent} [{', '.join(s.covers_objective_codes) or 'none'}]"
        for s in outline.sections if s is not section
    ) or "  (this is the only section)"
    prompt = load_prompt("write_section.txt").format(
        house_style=HOUSE_STYLE,
        spec_block=_spec_block(spec),
        heading=section.heading,
        intent=section.intent,
        codes=", ".join(section.covers_objective_codes) or "(none specified)",
        outline=others,
    )
    return call_model(client, label=f"section:{section.heading[:24]}", contents=prompt,
                      **_gen_config("model_write", "temperature_write", NoteSection))


def write_sections(client: genai.Client, spec: TopicSpec, outline: NotesOutline) -> list[NoteSection]:
    """Draft sections concurrently (mirrors grade_questions_parallel), order preserved."""
    results: list[NoteSection | None] = [None] * len(outline.sections)
    with ThreadPoolExecutor(max_workers=CONFIG["max_parallel_sections"]) as ex:
        futs = {ex.submit(write_section, client, spec, s, outline): i
                for i, s in enumerate(outline.sections)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
    return [r for r in results if r is not None]


def finalize_notes(client: genai.Client, spec: TopicSpec, sections: list[NoteSection]) -> NotesExtras:
    joined = "\n\n".join(f"## {s.heading}\n{s.body}" for s in sections)
    prompt = load_prompt("finalize.txt").format(
        house_style=HOUSE_STYLE, spec_block=_spec_block(spec), sections=joined
    )
    return call_model(client, label=f"finalize:{spec.topic_id}", contents=prompt,
                      **_gen_config("model_write", "temperature_write", NotesExtras))


def verify_coverage(client: genai.Client, spec: TopicSpec, sections: list[NoteSection]) -> CoverageReport:
    def _sec_text(s: NoteSection) -> str:
        calls = "".join(f"\n[{c.kind}] {c.title} {c.body}".rstrip() for c in s.callouts)
        return (
            f"## {s.heading} (claims: {', '.join(s.covers_objective_codes) or 'none'})\n"
            f"{s.body}{calls}"
        )

    joined = "\n\n".join(_sec_text(s) for s in sections)
    prompt = load_prompt("verify.txt").format(spec_block=_spec_block(spec), notes=joined)
    return call_model(client, label=f"verify:{spec.topic_id}", contents=prompt,
                      **_gen_config("model_verify", "temperature_verify", CoverageReport))


# ---------------------------------------------------------------------------
# Image search — Wikimedia Commons (primary) + Openverse (fallback), embedded as
# base64 so the HTML stays self-contained. Only freely/CC-licensed results.
# ---------------------------------------------------------------------------

_UA = "APGuru-ClassNotes/0.1 (https://apguru.com; info@apguru.com)"
_IMG_MIME_OK = ("image/png", "image/jpeg", "image/svg+xml", "image/gif")


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _wikimedia_candidates(query: str, n: int, width: int) -> list[dict]:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": "6", "gsrlimit": str(n),
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": str(width),
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    data = json.loads(_http_get(url))
    out: list[dict] = []
    for p in ((data.get("query") or {}).get("pages") or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        mime, thumb = ii.get("mime") or "", ii.get("thumburl")
        if not thumb or mime not in _IMG_MIME_OK:
            continue
        em = ii.get("extmetadata") or {}
        out.append({
            "thumb": thumb,
            # SVG/GIF thumbnails are rasterised to PNG by Commons.
            "mime": "image/png" if mime in ("image/svg+xml", "image/gif") else mime,
            "license": (em.get("LicenseShortName") or {}).get("value") or "see source",
            "artist": _strip_tags((em.get("Artist") or {}).get("value") or ""),
            "source": ii.get("descriptionurl") or "",
            "title": (p.get("title") or "").replace("File:", "").rsplit(".", 1)[0],
            "via": "Wikimedia Commons",
        })
    return out


def _openverse_candidates(query: str, n: int) -> list[dict]:
    params = {"q": query, "license_type": "commercial", "page_size": str(n)}
    url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params)
    try:
        data = json.loads(_http_get(url))
    except Exception:
        return []
    out: list[dict] = []
    for x in data.get("results", []):
        thumb = x.get("thumbnail") or x.get("url")
        if not thumb:
            continue
        out.append({
            "thumb": thumb, "mime": "image/jpeg",
            "license": (x.get("license") or "cc").upper(),
            "artist": x.get("creator") or "",
            "source": x.get("foreign_landing_url") or x.get("url") or "",
            "title": x.get("title") or query, "via": "Openverse",
        })
    return out


_STOP = {
    "a", "an", "the", "of", "with", "and", "or", "for", "to", "in", "on", "showing",
    "show", "labelled", "labeled", "diagram", "image", "picture", "photo", "photograph",
    "before", "after", "example", "typical", "cross", "section", "process", "effect",
    "between", "detailed", "simple", "clear",
}


def _simplify(query: str) -> list[str]:
    """Progressively simpler search variants (most specific first). Wikimedia's
    file-name search matches short keyword queries far better than long phrases."""
    words = re.findall(r"[A-Za-z0-9']+", query.lower())
    kw = [w for w in words if w not in _STOP]
    variants = [query.strip()]
    if kw:
        variants += [" ".join(kw), " ".join(kw[:3]), " ".join(kw[:2])]
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _search_images(query: str, n: int, width: int) -> list[dict]:
    """Wikimedia (with query simplification) augmented by Openverse when thin."""
    cands: list[dict] = []
    for variant in _simplify(query):
        try:
            cands = _wikimedia_candidates(variant, n=n, width=width)
        except Exception as exc:
            print(f"    wikimedia error for '{variant[:32]}': {exc}")
            cands = []
        if cands:
            break
    if len(cands) < 2:
        cands = cands + _openverse_candidates(query, n=n)
    return cands


def _data_uri(b: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(b).decode('ascii')}"


def _attribution(c: dict) -> str:
    who = f" by {c['artist']}" if c.get("artist") else ""
    lic = f", {c['license']}" if c.get("license") else ""
    title = c.get("title") or "Image"
    if c.get("source"):
        title = f'<a href="{c["source"]}" target="_blank" rel="noopener">{title}</a>'
    return f'{title}{who}{lic} (via {c.get("via", "the web")})'


def _select_image(client: genai.Client, query: str, caption: str, candidates: list[dict]) -> int:
    """Gemini vision picks the best candidate; returns 0-based index, or -1 for none."""
    import io
    from PIL import Image

    prompt = (
        "These study notes would benefit from an illustration for:\n"
        f"Caption: {caption}\nSearch query: {query}\n\n"
        f"{len(candidates)} candidate images follow, numbered 1..{len(candidates)}. Pick the one "
        "that fits best — a reasonably relevant, clear educational image is better than none, so "
        "lean towards choosing one. Only choose 0 if EVERY candidate is clearly off-topic, "
        "inappropriate, a joke, or misleading."
    )
    contents: list = [prompt]
    for i, c in enumerate(candidates, 1):
        contents.append(f"Image {i}:")
        try:
            contents.append(Image.open(io.BytesIO(c["_bytes"])))
        except Exception:
            contents.append(f"(image {i} could not be read)")
    choice = call_model(client, label="img-select", contents=contents,
                        **_gen_config("model_vision", "temperature_verify", ImageChoice))
    return choice.choice - 1 if 1 <= choice.choice <= len(candidates) else -1


def fetch_images_for_sections(client: genai.Client, sections: list[NoteSection], *,
                              max_images: int, width: int) -> int:
    """Search, select and embed images for `image` diagrams in place; returns count embedded."""
    slots = [d for s in sections for d in s.diagrams if d.kind == "image" and not d.image_src]
    embedded = 0
    for d in slots:
        if embedded >= max_images:
            break
        query = (d.content or d.caption or "").strip()
        if not query:
            continue
        try:
            cands = _search_images(query, n=8, width=width)[:6]
            for c in cands:
                try:
                    c["_bytes"] = _http_get(c["thumb"])
                except Exception:
                    c["_bytes"] = b""
            cands = [c for c in cands if c.get("_bytes")]
            if not cands:
                print(f"    image: nothing usable for '{query[:40]}'")
                continue
            idx = _select_image(client, query, d.caption, cands) if CONFIG.get("image_vision_select") else 0
            if idx < 0:
                print(f"    image: no suitable match for '{query[:40]}'")
                continue
            c = cands[idx]
            d.image_src = _data_uri(c["_bytes"], c["mime"])
            d.attribution = _attribution(c)
            embedded += 1
            print(f"    image: '{query[:38]}' -> {c['title'][:36]} ({c['license']})")
        except Exception as exc:
            print(f"    image fetch failed for '{query[:40]}': {exc}")
    return embedded


def generate_notes(client: genai.Client, spec: TopicSpec) -> ClassNotes:
    """Run the full pipeline for one topic and assemble the ClassNotes."""
    print(f"[1/4] outline   {spec.topic_id}")
    outline = generate_outline(client, spec)
    print(f"      planned {len(outline.sections)} section(s)")

    print(f"[2/4] write     {len(outline.sections)} section(s) in parallel")
    sections = write_sections(client, spec, outline)

    if CONFIG.get("image_search"):
        print("[img]  fetching relevant images (Wikimedia Commons / Openverse)")
        try:
            k = fetch_images_for_sections(client, sections,
                                          max_images=CONFIG["max_images_per_topic"],
                                          width=CONFIG["image_width"])
            print(f"       embedded {k} image(s)")
        except Exception as exc:
            print(f"       image stage skipped: {exc}")

    print("[3/4] finalize  overview, key terms, misconceptions, exam tips, practice")
    extras = finalize_notes(client, spec, sections)

    print(f"[4/4] verify    coverage of {len(spec.learning_objectives)} objective(s)")
    coverage = verify_coverage(client, spec, sections)

    # Collect review flags: uncovered objectives + low-confidence sections + verifier flags.
    flags = list(coverage.review_flags)
    uncovered = [c.code for c in coverage.items if not c.covered]
    if uncovered:
        flags.append(f"Objectives not fully covered: {', '.join(uncovered)}")
    for s in sections:
        if s.confidence == "low":
            flags.append(f"Low-confidence section: {s.heading}")

    return ClassNotes(
        topic_id=spec.topic_id, board=spec.board, subject=spec.subject, level=spec.level,
        unit=spec.unit, topic=spec.topic, learning_objectives=spec.learning_objectives,
        overview=extras.overview, key_terms=extras.key_terms, sections=sections,
        common_misconceptions=extras.common_misconceptions, exam_tips=extras.exam_tips,
        practice_questions=extras.practice_questions, summary=extras.summary,
        coverage_report=coverage.items, review_flags=flags,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# ---------------------------------------------------------------------------
# Render + save
# ---------------------------------------------------------------------------

# Math spans we must NOT touch when sanitizing prose: display $$...$$ and the
# backslash forms \(...\) / \[...\]. Protecting these keeps LaTeX commands that
# begin with \n (e.g. \neq, \nabla) intact when we unescape stray newlines.
_MATH = re.compile(r"\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]")
# Tabular LaTeX that MathJax cannot render as inline/display math.
_LATEX_TABLE = re.compile(r"\\hline|\\begin\{tabular\}|\\begin\{array\}")


def _clean_md(s: str) -> str:
    """Sanitize a model-produced text field for browser rendering.

    The model sometimes emits the two characters ``\\n`` instead of a real
    newline; convert those (and ``\\t``) to real whitespace, while protecting
    math spans so LaTeX commands beginning with ``\\n`` are left untouched.
    """
    if not s:
        return s
    spans: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    protected = _MATH.sub(_stash, s)
    protected = protected.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], protected)


# Callout kinds -> (emoji, default label). The leading emoji is also how the HTML
# colourises each callout box (see _HTML_SHELL).
_CALLOUT = {
    "tip": ("💡", "Quick Tip"),
    "mistake": ("⚠️", "Common Mistake"),
    "formula": ("📐", "Key Formula / Fact"),
    "remember": ("🧠", "Remember"),
}


_MERMAID_LABEL = re.compile(r'(?<!\[)\[([^\[\]"]+)\](?!\])')


def _sanitize_mermaid(src: str) -> str:
    """Quote square-bracket node labels so parentheses / '+' / punctuation don't
    break Mermaid's parser (e.g. `A[Glucose (6C)]` -> `A["Glucose (6C)"]`)."""
    return _MERMAID_LABEL.sub(lambda m: f'["{m.group(1).strip()}"]', src)


def render_markdown(n: ClassNotes) -> str:
    L: list[str] = []
    L.append(f"# {n.topic}")
    L.append(f"*{n.board} · {n.subject} · {n.level} — {n.unit}*\n")
    L.append(_clean_md(n.overview) + "\n")

    # Objective *codes* are internal grounding IDs — keep them in the JSON, not
    # in the student-facing notes. Tier (Core/Supplement) stays; it's pedagogical.
    L.append("## Learning objectives")
    for lo in n.learning_objectives:
        tier = f" _({lo.tier})_" if lo.tier else ""
        L.append(f"- {lo.statement}{tier}")
    L.append("")

    if n.key_terms:
        L.append("## Key terms")
        for t in n.key_terms:
            L.append(f"- **{t.term}** — {_clean_md(t.definition)}")
        L.append("")

    for s in n.sections:
        L.append(f"## {s.heading}")
        L.append(_clean_md(s.body))
        for c in s.callouts:
            emoji, label = _CALLOUT.get(c.kind, ("📌", "Note"))
            # The category label ("💡 Quick Tip") is always the box title. Any custom
            # title the model gave becomes a bold lead-in to the body. Prefix every line
            # with "> " so the whole callout (title + body) stays in one blockquote box.
            lead = f"**{_clean_md(c.title.strip())}:** " if c.title.strip() else ""
            body = (lead + _clean_md(c.body)).replace("\n", "\n> ")
            L.append(f"\n> **{emoji} {label}**\n>\n> {body}")
        for d in s.diagrams:
            if d.kind == "mermaid":
                L.append(f"\n```mermaid\n{_sanitize_mermaid(d.content)}\n```")
                L.append(f"*{d.caption}*")
            elif d.kind == "latex" and not _LATEX_TABLE.search(d.content):
                L.append(f"\n$$\n{d.content}\n$$")
                L.append(f"*{d.caption}*")
            elif d.kind == "latex":
                # MathJax can't render tabular/\hline as math — show as code, not an error.
                L.append(f"\n**{d.caption}:**\n\n```\n{d.content}\n```")
            elif d.kind == "image" and d.image_src:
                alt = (d.caption or "figure").replace('"', "'")
                credit = f' <span class="credit">— {d.attribution}</span>' if d.attribution else ""
                L.append(
                    f'\n<figure class="note-img">'
                    f'<img src="{d.image_src}" alt="{alt}" loading="lazy">'
                    f'<figcaption>{_clean_md(d.caption)}{credit}</figcaption></figure>'
                )
            elif d.kind == "image":
                # No suitable free image found — degrade to a placeholder for the teacher.
                L.append(f"\n> **Suggested image — {d.caption}:** {_clean_md(d.content)}")
            else:
                L.append(f"\n> **Diagram — {d.caption}:** {_clean_md(d.content)}")
        for ex in s.worked_examples:
            L.append(f"\n**Worked example.** {_clean_md(ex.prompt)}\n\n{_clean_md(ex.solution)}")
        L.append("")

    if n.common_misconceptions:
        L.append("## Common misconceptions")
        for m in n.common_misconceptions:
            L.append(f"- {_clean_md(m)}")
        L.append("")

    if n.exam_tips:
        L.append("## Exam tips")
        for t in n.exam_tips:
            L.append(f"- {_clean_md(t)}")
        L.append("")

    if n.practice_questions:
        L.append("## Practice questions")
        for i, q in enumerate(n.practice_questions, 1):
            L.append(f"\n**Q{i}.** {_clean_md(q.question)}\n")
            L.append(f"<details><summary>Worked solution</summary>\n\n{_clean_md(q.worked_solution)}\n\n</details>")
        L.append("")

    L.append("## Summary")
    L.append(_clean_md(n.summary))

    covered = sum(1 for c in n.coverage_report if c.covered)
    total = len(n.coverage_report)
    L.append(f"\n---\n*Coverage: {covered}/{total} learning objectives. Generated {n.generated_at}.*")
    if n.review_flags:
        L.append("\n**⚠ Review flags (check before classroom use):**")
        for f in n.review_flags:
            L.append(f"- {_clean_md(f)}")
    return "\n".join(L)


# Self-contained HTML: marked renders the Markdown, MathJax renders LaTeX,
# Mermaid renders ```mermaid blocks. All from CDN, so no Python deps.
_HTML_SHELL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script>window.MathJax={tex:{inlineMath:[['\\(','\\)']],displayMath:[['$$','$$'],['\\[','\\]']]},svg:{fontCache:'global'}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
 body{max-width:840px;margin:2rem auto;padding:0 1rem;font:16px/1.65 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a}
 h1{margin-bottom:.1rem} h2{margin-top:2.2rem;border-bottom:1px solid #eee;padding-bottom:.3rem}
 code{background:#f4f4f5;padding:.1rem .35rem;border-radius:4px;font-size:.92em}
 pre{background:#f7f7f8;padding:1rem;border-radius:8px;overflow:auto} pre code{background:none;padding:0}
 details{margin:.4rem 0;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:.4rem .8rem}
 summary{cursor:pointer;font-weight:600} blockquote{border-left:3px solid #d0d7de;margin:.6rem 0;padding:.2rem 1rem;color:#555}
 em{color:#666} .mermaid{margin:1rem 0;text-align:center}
 pre.mermaid-fallback{background:#fff8f0;border:1px dashed #e0a030;border-radius:6px;color:#6a5a3a;font-size:.85em}
 blockquote.callout{border:1px solid #d0d7de;border-left-width:6px;border-radius:8px;padding:.5rem 1rem;margin:1rem 0;color:#1f2328}
 blockquote.callout p{margin:.45rem 0}
 blockquote.callout p:first-child{font-weight:700}
 blockquote.callout.tip{border-left-color:#0969da;background:#ddf4ff}
 blockquote.callout.tip p:first-child{color:#0969da}
 blockquote.callout.mistake{border-left-color:#cf222e;background:#ffebe9}
 blockquote.callout.mistake p:first-child{color:#cf222e}
 blockquote.callout.formula{border-left-color:#1a7f37;background:#dafbe1}
 blockquote.callout.formula p:first-child{color:#1a7f37}
 blockquote.callout.remember{border-left-color:#8250df;background:#fbefff}
 blockquote.callout.remember p:first-child{color:#8250df}
 figure.note-img{margin:1.3rem auto;text-align:center}
 figure.note-img img{max-width:100%;height:auto;border:1px solid #e5e7eb;border-radius:8px;background:#fff}
 figure.note-img figcaption{font-size:.9em;color:#57606a;margin-top:.45rem}
 figure.note-img .credit{color:#8b949e}
</style></head>
<body><div id="content">Rendering…</div>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
mermaid.initialize({startOnLoad:false,securityLevel:'loose'});
const md = __MD_JSON__;
marked.setOptions({gfm:true,breaks:false});
// Protect math spans before marked runs: CommonMark would strip the backslashes
// from \(...\). A bare $ then passes through as literal currency, not math.
const MATH=[];
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const src = md.replace(/\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g,
  m=>{MATH.push(m); return `@@MATH${MATH.length-1}@@`;});
const html = marked.parse(src).replace(/@@MATH(\d+)@@/g,(_,i)=>esc(MATH[i]));
document.getElementById('content').innerHTML = html;
// Colourise callout blockquotes by the emoji that starts their title line.
const CT=[['💡','tip'],['⚠','mistake'],['📐','formula'],['🧠','remember']];
document.querySelectorAll('#content blockquote').forEach(bq=>{
  const t=(bq.textContent||'').trim();
  for(const [e,k] of CT){ if(t.startsWith(e)){ bq.classList.add('callout',k); break; } }
});
document.querySelectorAll('code.language-mermaid').forEach((c)=>{
  const d=document.createElement('div'); d.className='mermaid'; d.textContent=c.textContent;
  (c.closest('pre')||c).replaceWith(d);
});
(async () => {
  for (const el of document.querySelectorAll('.mermaid')) {
    let ok = true;
    try { ok = (await mermaid.parse(el.textContent, {suppressErrors:true})) !== false; }
    catch (e) { ok = false; }
    if (!ok) {  // invalid diagram -> show the source in a soft box, never the error bomb
      const pre = document.createElement('pre');
      pre.className = 'mermaid-fallback'; pre.textContent = el.textContent;
      el.replaceWith(pre);
    }
  }
  try { await mermaid.run(); } catch (e) {}
  if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise();
})();
</script></body></html>"""


def render_html(n: ClassNotes) -> str:
    md = render_markdown(n)
    return _HTML_SHELL.replace("__TITLE__", f"{n.topic} — {n.board}").replace("__MD_JSON__", json.dumps(md))


def save_notes(n: ClassNotes, out_dir: str | None = None) -> dict[str, str]:
    out = Path(out_dir or CONFIG["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    base = out / n.topic_id
    paths = {
        "md": str(base.with_suffix(".md")),
        "html": str(base.with_suffix(".html")),
        "json": str(base.with_suffix(".json")),
    }
    base.with_suffix(".md").write_text(render_markdown(n), encoding="utf-8")
    base.with_suffix(".html").write_text(render_html(n), encoding="utf-8")
    base.with_suffix(".json").write_text(n.model_dump_json(indent=2), encoding="utf-8")
    return paths
