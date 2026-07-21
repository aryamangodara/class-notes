"""Shared core logic for the Class Notes generator.

Thin CLI/notebook orchestration (``notes.py``) sits on top; the interactive v2
pipeline (``pipeline_v2.py``) builds on the utilities here — the Gemini client +
retry wrapper, structured-output config, prompt loading, curriculum grounding,
the outline stage, and the Wikimedia/Openverse image search. Mirrored from
``Grader/helpers.py`` so this stays plug-compatible with the existing stack:
same auth, same models, same structured-output convention (Pydantic
``response_schema`` -> ``response.parsed``).

The rendering + assembly of the notes themselves lives in the v2 modules
(``pipeline_v2.py`` / ``render_v2.py`` / ``schemas_v2.py``), not here.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from contextlib import nullcontext
from pathlib import Path

from google import genai
from google.genai import types

from config import CONFIG, HOUSE_STYLE
from schemas import ImageChoice, NotesOutline, TopicSpec

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Global concurrency governor: caps simultaneous Gemini calls across the WHOLE process
# (batch --jobs x the intra-topic section/stage pools) so aggregate load can't exceed
# provider quota. Acquired only around the API call itself — never held during backoff
# sleeps — so a thread waiting for a permit never holds one. 0/absent => unlimited.
_MAX_INFLIGHT = CONFIG.get("max_inflight_model_calls", 0) or 0
_INFLIGHT_SEM = threading.BoundedSemaphore(_MAX_INFLIGHT) if _MAX_INFLIGHT > 0 else None


# ---------------------------------------------------------------------------
# Langfuse tracing — the contract every model call must satisfy
# ---------------------------------------------------------------------------
# DOCTRINE: not a single model call goes out untraced. Every Gemini call flows through
# call_model, which REQUIRES a `trace` contract built by the builders below — there is
# no default and no opt-out, so a new call site cannot silently ship an uncosted call.
# `trace_for` validates the feature/stage vocabulary, so a typo fails loudly on the
# first call instead of landing in Langfuse as an anonymous "unknown".
#
# The contract's shape is dictated by how Langfuse aggregates:
#   feature     -> the TRACE name. Low-cardinality (notes.generate, notes.ground_specs):
#                  this is what "cost per product surface" groups by.
#   stage       -> the OBSERVATION name, prefixed `notes.` (notes.section, notes.coverage).
#                  Also low-cardinality ON PURPOSE — it is the "cost per pipeline step"
#                  axis. The variable part (topic, heading) belongs in tags/metadata; a
#                  name like `v2-section:Periodic Trends and Co` aggregates to nothing.
#   group       -> the unit of work one trace covers (a topic id, a subject spec run).
#                  Seeds a DETERMINISTIC trace id, so every stage of one topic rolls up
#                  under one trace + one cost total, without OTel context — which matters
#                  because the pipeline fans out across threads (--jobs x the section
#                  pool) and contextvars do NOT cross a ThreadPoolExecutor boundary.
#   tags        -> the filter axes in the UI (app/feature/stage/board/subject/level/topic).
#   metadata    -> the same dimensions as data, plus per-stage detail.
#
# Transmission is best-effort and NEVER breaks a run (a dropped cost record must not cost
# a topic); the CONTRACT above is validated deterministically. Fully optional: a no-op
# unless LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are set (loaded from .env by the CLI
# before the first call). Langfuse prices the models itself, so sending token counts is
# enough for total / per-subject / per-stage cost to roll up.

# The product surfaces that spend tokens -> Langfuse TRACE names. A new CLI that calls a
# model adds itself here.
FEATURE_GENERATE = "notes.generate"          # notes.py: topic -> interactive notes
FEATURE_EXTRACT = "notes.extract_specs"      # extract_specs.py: spec PDF -> TopicSpecs
FEATURE_GROUND = "notes.ground_specs"        # ground_specs.py: verify codes vs spec PDF
FEATURES = frozenset({FEATURE_GENERATE, FEATURE_EXTRACT, FEATURE_GROUND})

# The pipeline steps -> Langfuse OBSERVATION names (rendered as `notes.<stage>`). Keep
# this in lockstep with the call sites; `tests/_smoke_tracing.py` asserts every call site's
# stage is declared here AND that every stage here is reachable from a call site.
STAGES = frozenset({
    "outline", "section", "coverage", "practice", "finalize",   # pipeline_v2
    "image.select",                                             # helpers._select_image
    "past_papers.candidates", "past_papers.verify",             # past_papers
    "spec.enumerate", "spec.extract", "spec.ground",            # extract_specs / ground_specs
})

_LF = "uninit"           # "uninit" -> not yet built; None -> disabled; else the client
_LF_LOCK = threading.Lock()
_LF_WARNED = False
_USAGE_WARNED = False


def _usage_details(um) -> "tuple[dict, int]":
    """Map Gemini ``usage_metadata`` -> Langfuse ``usage_details``, using the key names
    Langfuse actually PRICES for Gemini. Pure + duck-typed, so the smoke test pins the
    arithmetic offline.

    Gemini reports billable tokens in FOUR buckets, and a call is only costed correctly
    if all four are sent under the names Langfuse prices:

      prompt_token_count         -> ``input``   (INCLUDES the cached prefix — see below)
      cached_content_token_count -> priced separately at ~10% of the input rate
      candidates_token_count     -> ``output``  (the VISIBLE answer ONLY)
      thoughts_token_count       -> ``output_reasoning_tokens``: billed at the OUTPUT
                                    rate, and NOT part of candidates_token_count

    That last one is the trap this function exists for: on a thinking model the reasoning
    tokens routinely run 2x the visible output (measured: 211% on gemini-3.1-pro-preview,
    185% on gemini-3.5-flash), so sending only prompt+candidates under-reports the true
    bill by roughly half. ``total_token_count`` == prompt + candidates + thoughts, which
    is exactly the cross-check the residual below performs.

    Key names are not free-form: Langfuse matches each one EXACTLY against the model
    definition's per-usage-type prices (an unmatched key silently costs $0 — the same
    hole), and it buckets a usage type as input/output by whether the NAME CONTAINS
    "input"/"output". ``output_reasoning_tokens`` and ``thoughts_token_count`` are priced
    identically on every model here, so the former is chosen: it costs the same AND rolls
    the reasoning tokens into Langfuse's output-token totals, which is what makes those
    totals reconcile against Vertex instead of reading ~1/3 low.

    Returns ``(usage, residual)``. A non-zero residual means Gemini grew a NEW billable
    token class that none of our keys claimed — i.e. the under-reporting hole has silently
    reopened — so it is surfaced by the caller, never swallowed.
    """
    def n(attr: str) -> int:
        try:
            return int(getattr(um, attr, 0) or 0)
        except (TypeError, ValueError):
            return 0

    prompt, cached = n("prompt_token_count"), n("cached_content_token_count")
    output, thoughts = n("candidates_token_count"), n("thoughts_token_count")
    tool = n("tool_use_prompt_token_count")
    # Gemini counts the cached prefix INSIDE prompt_token_count but bills it at ~10%, and
    # Langfuse prices `cached_content_token_count` as its own key — so `input` must carry
    # only the UNCACHED remainder, or every cached token gets charged at the full rate.
    usage = {"input": max(prompt - cached, 0) + tool, "output": output}
    if cached:
        usage["cached_content_token_count"] = cached
    if thoughts:
        usage["output_reasoning_tokens"] = thoughts
    # Deliberately NO `total` key: Langfuse derives the total by summing these, and a model
    # definition that also prices `total` would double-charge every call if we sent one.
    total = n("total_token_count")
    return usage, (total - sum(usage.values()) if total else 0)


def trace_for(feature: str, stage: str, *, group: str, board: str = "", subject: str = "",
              level: str = "", topic_id: str = "", **detail) -> dict:
    """Build the tracing contract REQUIRED by ``call_model`` (see the doctrine above).

    Rejects an undeclared feature/stage rather than emitting an unattributable call —
    tracing is a contract, not a best-effort afterthought. Prefer the per-feature
    builders below; reach for this directly only when adding a feature.
    """
    if feature not in FEATURES:
        raise ValueError(f"untraceable feature {feature!r} — add it to helpers.FEATURES")
    if stage not in STAGES:
        raise ValueError(f"untraceable stage {stage!r} — add it to helpers.STAGES")
    if not group:
        raise ValueError(f"trace_for({feature}, {stage}) needs a non-empty `group` to roll up under")
    dims = [("board", board), ("subject", subject), ("level", level), ("topic", topic_id)]
    # Langfuse DROPS a propagated value that is not a str or exceeds 200 chars — silently.
    # So coerce and clip everything here: a quietly missing tag is exactly the blind spot
    # this contract exists to remove. (The trace-id seed uses the unclipped `group`.)
    meta = {"feature": feature, "stage": stage, "group": str(group)[:200]}
    meta.update({k: str(v)[:200] for k, v in dims if v})
    meta.update({k: str(v)[:200] for k, v in detail.items() if v})
    return {
        "feature": feature,
        "stage": stage,
        "group": group,
        "observation": f"notes.{stage}",
        "tags": ["app:class-notes", f"feature:{feature}", f"stage:{stage}"]
                + [f"{k}:{str(v)[:190]}" for k, v in dims if v],
        "metadata": meta,
        # The console label for retry/error lines — derived, so it can never drift from
        # what Langfuse shows. `item` is the optional "which one" detail (a section
        # heading, a spec topic name) that distinguishes sibling calls of one stage.
        "label": f"{stage}:{detail.get('item') or topic_id or group}",
    }


def trace_topic(spec, stage: str, **detail) -> dict:
    """Tracing contract for a notes-generation call: one trace per topic, so every
    stage (outline -> section -> coverage -> images -> practice -> finalize -> papers)
    and its cost roll up under that topic."""
    return trace_for(FEATURE_GENERATE, stage, group=spec.topic_id, board=spec.board,
                     subject=spec.subject, level=spec.level, topic_id=spec.topic_id, **detail)


def trace_spec_run(stage: str, *, board: str, subject: str, level: str = "", **detail) -> dict:
    """Tracing contract for an extract_specs call: one trace per SUBJECT SPEC run, so the
    enumerate + every per-topic extract from one PDF roll up under one trace and one cost.
    (They used to mint a fresh single-call trace each, named after the topic — ~60 unique
    trace names per subject, which aggregates to nothing.)"""
    return trace_for(FEATURE_EXTRACT, stage, group=f"{board}:{subject}", board=board,
                     subject=subject, level=level, **detail)


def _langfuse():
    """Lazily build the Langfuse client from env (after load_dotenv). None if the keys are
    absent or the SDK isn't installed."""
    global _LF
    if _LF == "uninit":
        with _LF_LOCK:
            if _LF == "uninit":
                _LF = None
                if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
                    try:
                        from langfuse import Langfuse
                        _LF = Langfuse()
                        atexit.register(flush_langfuse)
                        print("    Langfuse cost tracking: ON")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    Langfuse cost tracking OFF (init failed: {exc})")
                        _LF = None
                else:
                    # Say so LOUDLY. A silently-untracked run is the same blind spot as an
                    # untraced call: the spend still happens, it just never reaches the
                    # cost dashboard (this is why `spec.ground` had zero observations —
                    # ground_specs.py only ever ran from a .env with no Langfuse keys).
                    print("    Langfuse cost tracking OFF (no LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY "
                          "in .env) — this run's model spend will NOT be recorded anywhere")
    return _LF


def _log_generation(trace: dict, model, resp) -> None:
    """Record one Gemini call as a Langfuse generation (model + token usage -> cost),
    named + tagged per the `trace` contract.

    ``propagate_attributes`` is the ONLY v4 API that sets a trace name (v3's
    ``update_current_trace`` / ``span.update_trace`` are gone). Without it a
    ``trace_context`` trace has NO name and every call in it renders as "unknown" — the
    bug this replaces. It is contextvar-scoped, so it must wrap ``start_observation`` in
    the SAME thread; it does here, because this runs inline in the worker that made the
    call, never on a parent thread.
    """
    lf = _langfuse()
    if lf is None:
        return
    global _LF_WARNED
    try:
        from langfuse import propagate_attributes

        um = getattr(resp, "usage_metadata", None)
        usage = None
        if um is not None:
            usage, residual = _usage_details(um)
            if residual:
                # Google billed tokens none of our keys claimed -> a new token class.
                # Loud once, because silence here is how the thinking-token hole hid.
                global _USAGE_WARNED
                if not _USAGE_WARNED:
                    print(f"    Langfuse usage gap: {residual} token(s) of {model} unmapped "
                          f"(usage_metadata grew a billable field — see helpers._usage_details); "
                          f"cost is UNDER-reported until it is mapped")
                    _USAGE_WARNED = True
        meta = trace["metadata"]
        with propagate_attributes(trace_name=trace["feature"], tags=list(trace["tags"]),
                                  metadata=meta, session_id=trace["group"]):
            lf.start_observation(
                name=trace["observation"], as_type="generation", model=model,
                usage_details=usage, metadata=meta,
                # Deterministic trace per unit of work — every stage of one topic rolls
                # up under one trace + its total cost. Thread-safe: seeded, not OTel.
                trace_context={"trace_id": lf.create_trace_id(seed=trace["group"])},
            ).end()
    except Exception as exc:  # noqa: BLE001 — cost tracking must NEVER break a run
        if not _LF_WARNED:
            print(f"    Langfuse logging error (further errors suppressed): {exc}")
            _LF_WARNED = True


def flush_langfuse() -> None:
    """Send buffered Langfuse events. Call at the end of a batch (also runs atexit) so a
    CLI doesn't exit before the tail of the trace uploads."""
    lf = _LF
    if lf and lf != "uninit":
        try:
            lf.flush()
        except Exception:  # noqa: BLE001
            pass




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
_RETRYABLE_WORDS = ("UNAVAILABLE", "DEADLINE", "RESOURCE_EXHAUSTED", "INTERNAL", "CANCELLED",
                    "ABORTED", "CONNECTION RESET", "FORCIBLY CLOSED", "CONNECTION ABORTED",
                    "BROKEN PIPE", "10054")
# httpx / socket transport failures — common under parallel load when the provider drops
# a connection mid-call. These are transient, so retry rather than fail the whole topic
# (a dropped connection once killed a topic in an otherwise-good --jobs 3 batch).
_RETRYABLE_EXC_NAMES = ("ReadError", "WriteError", "ConnectError", "ConnectTimeout",
                        "ReadTimeout", "PoolTimeout", "RemoteProtocolError",
                        "ConnectionError", "ConnectionResetError")


def _transient(exc: Exception) -> bool:
    if type(exc).__name__ in _RETRYABLE_EXC_NAMES:
        return True
    code = getattr(exc, "code", None)
    blob = str(exc).upper()
    if isinstance(code, int) and code in _RETRYABLE:
        return True
    return any(w in blob for w in _RETRYABLE_WORDS) or any(str(c) in blob for c in _RETRYABLE)


def call_model(client: genai.Client, *, trace: dict, max_attempts: int = 4,
               base_delay: float = 2.0, **kwargs):
    """Call ``client.models.generate_content`` with retry, returning the parsed
    Pydantic object (``response.parsed``).

    Retries transient API errors (429/499/5xx, CANCELLED/UNAVAILABLE/...) and
    empty structured responses (``parsed is None`` — usually a safety filter or
    a MAX_TOKENS truncation) with exponential backoff + jitter. Non-transient
    errors (400, auth) raise immediately.

    ``trace`` (from ``trace_topic`` / ``trace_for``) is REQUIRED and has no default:
    this is the single call site for every Gemini call in the repo, so demanding it
    here is what makes "no untraced call" structural rather than a convention. It also
    supplies the console label, so the logs and Langfuse can never disagree.
    """
    tag = f" [{trace['label']}]"
    for attempt in range(1, max_attempts + 1):
        try:
            # Bound total in-flight calls across all batch/section/stage threads.
            with (_INFLIGHT_SEM if _INFLIGHT_SEM is not None else nullcontext()):
                resp = client.models.generate_content(**kwargs)
        except Exception as exc:  # noqa: BLE001 — classify then re-raise
            if attempt == max_attempts or not _transient(exc):
                raise
            delay = base_delay * 2 ** (attempt - 1) + random.uniform(0, 1)
            print(f"    transient error{tag} (attempt {attempt}/{max_attempts}): {exc}; retrying in {delay:.1f}s")
            time.sleep(delay)
            continue

        # Cost tracking: log token usage for every response we got (even a truncated one
        # consumed tokens — a retry that lands here twice must show up twice, or the
        # cost of a flaky topic under-reports). Best-effort; never raises.
        _log_generation(trace, kwargs.get("model"), resp)
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
    # Canonical constants shared by EVERY stage — this is what stops a worked example
    # and a practice question disagreeing on the same value (e.g. a bond enthalpy).
    ref = (
        "\nREFERENCE DATA (use these EXACT values wherever the quantity appears — in "
        "worked examples AND practice questions; never substitute a different value):\n"
        f"{spec.reference_data}\n"
    ) if spec.reference_data.strip() else ""
    return (
        f"BOARD: {spec.board}\nSUBJECT: {spec.subject}\nLEVEL: {spec.level}\n"
        f"UNIT: {spec.unit}\nTOPIC: {spec.topic}\n"
        f"PREREQUISITES (assume known, do not re-teach): {prereqs}\n\n"
        f"DEPTH PROFILE (calibrate exactly to this):\n{spec.depth_profile}\n\n"
        f"ASSESSMENT NOTES (teach toward this):\n{spec.assessment_notes}\n\n"
        f"LEARNING OBJECTIVES (the contract — cover all, exceed none):\n{los}\n"
        f"{ref}"
    )


# ---------------------------------------------------------------------------
# Outline stage (plan sections covering every objective) — shared by the pipeline
# ---------------------------------------------------------------------------

def generate_outline(client: genai.Client, spec: TopicSpec) -> NotesOutline:
    prompt = load_prompt("outline.txt").format(house_style=HOUSE_STYLE, spec_block=_spec_block(spec))
    return call_model(client, trace=trace_topic(spec, "outline"), contents=prompt,
                      **_gen_config("model_plan", "temperature_plan", NotesOutline))


# ---------------------------------------------------------------------------
# Image search — Wikimedia Commons (primary) + Openverse (fallback), embedded as
# base64 so the HTML stays self-contained. Only freely/CC-licensed results.
# ---------------------------------------------------------------------------

_UA = "APGuru-ClassNotes/0.1 (https://apguru.com; info@apguru.com)"
_IMG_MIME_OK = ("image/png", "image/jpeg", "image/svg+xml", "image/gif")


def _http_get(url: str, timeout: int = 30, *, max_bytes: int | None = None,
              accept_types: "tuple[str, ...] | None" = None) -> bytes:
    """Fetch bytes with our User-Agent.

    Optional guards for UNTRUSTED fetches (e.g. paper PDFs): ``accept_types`` rejects
    a mismatched Content-Type (an HTML login page where a PDF was expected), and
    ``max_bytes`` caps the read so a hostile/huge URL can't exhaust memory. The image
    pipeline calls this with defaults, so its behaviour is unchanged.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if accept_types:
            ctype = (r.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
            if ctype and ctype not in accept_types:
                raise ValueError(f"unexpected content-type {ctype!r} for {url[:60]} (want {accept_types})")
        if max_bytes is not None:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"response exceeds max_bytes={max_bytes} for {url[:60]}")
            return data
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


def _select_image(client: genai.Client, query: str, caption: str, candidates: list[dict],
                  trace: dict) -> int:
    """Gemini vision picks the best candidate; returns 0-based index, or -1 for none.

    ``trace`` is threaded from the owning topic so the vision calls cost against that
    topic — they used to be the pipeline's one anonymous stage, one orphan trace each.
    """
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
    choice = call_model(client, trace=trace, contents=contents,
                        **_gen_config("model_vision", "temperature_verify", ImageChoice))
    return choice.choice - 1 if 1 <= choice.choice <= len(candidates) else -1
