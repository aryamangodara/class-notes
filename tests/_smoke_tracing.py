"""Offline oracle for the Langfuse tracing contract (no key/network).

The doctrine (helpers.py, CLAUDE.md): **not a single model call goes out untraced**, and
every call names its feature + stage and carries its filter tags. That rule is only worth
anything if it is mechanical, so this file is the enforcement:

  - a STATIC scan of every `call_model(...)` site in src/ — the rule cannot be broken by
    a new call site, because the scan reads the source, not a code path a test happens
    to exercise;
  - the signature check that makes `trace` structurally un-omittable;
  - stage-vocabulary parity (helpers.STAGES === the stages the call sites actually use),
    the same lockstep contract schemas_v2.BLOCK_TYPES has with renderBlock;
  - the contract's shape + the Langfuse value limits that silently DROP a bad tag.

Regression guarded: every call used to land in a trace with no name (the v4
`trace_context` path never sets one) and 4 call sites passed no metadata at all, so
Langfuse showed anonymous "unknown" calls — $24 of spend in null-named traces.
"""
import ast
import inspect
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import helpers  # noqa: E402

_SRC = os.path.join(_ROOT, "src")
_BUILDERS = {"trace_for", "trace_topic", "trace_spec_run"}
# Where the stage literal sits in each builder's positional args.
_STAGE_ARG = {"trace_for": 1, "trace_topic": 1, "trace_spec_run": 0}


def _fname(node):
    """The bare function name of a Call node (`helpers.call_model` -> `call_model`)."""
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _src_trees():
    for name in sorted(os.listdir(_SRC)):
        if name.endswith(".py"):
            path = os.path.join(_SRC, name)
            with open(path, encoding="utf-8") as fh:
                yield name, ast.parse(fh.read(), filename=path)


# 1. THE RULE: every call_model call site in src/ passes `trace=`. A static scan, so it
#    holds for call sites no test ever runs (the image/spec CLIs were exactly that).
_sites, _untraced = [], []
for _mod, _tree in _src_trees():
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Call) or _fname(_node) != "call_model":
            continue
        _sites.append(f"{_mod}:{_node.lineno}")
        if not any(k.arg == "trace" for k in _node.keywords):
            _untraced.append(f"{_mod}:{_node.lineno}")
assert not _untraced, (
    "UNTRACED model call(s) — every call_model site must pass trace=helpers.trace_* "
    f"(see the tracing doctrine in helpers.py): {_untraced}")
# Canary: a scan that matches nothing would "pass" every check above it. Assert it found
# call sites at all — but never a fixed count, or removing a stage fails an unrelated test.
assert _sites, "found ZERO call_model sites — the AST scan is broken, not the code"
print(f"no-untraced-call OK ({len(_sites)} call_model sites in src/, all pass trace=)")

# 2. The other half of "no untraced call": call_model must be the ONLY door to the API.
#    Check 1 is worthless if a stage can reach generate_content directly, so pin that
#    there is exactly one such call site and that it lives inside call_model itself.
_direct = []
for _mod, _tree in _src_trees():
    _fns = [n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)]
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Call) or _fname(_node) != "generate_content":
            continue
        _owner = next((f.name for f in _fns
                       if f.lineno <= _node.lineno <= (f.end_lineno or f.lineno)), "<module>")
        _direct.append((_mod, _owner, _node.lineno))
assert [(m, o) for m, o, _ in _direct] == [("helpers.py", "call_model")], (
    "every Gemini call must go through helpers.call_model — the one door that demands a "
    f"trace. Direct generate_content call site(s): {_direct}")
print(f"single-door OK (generate_content reachable only via helpers.call_model:{_direct[0][2]})")

# 3. ...and `trace` is structurally un-omittable: keyword-only with NO default, so a new
#    call site that forgets it fails at the call, not silently in Langfuse.
_p = inspect.signature(helpers.call_model).parameters
assert "trace" in _p, "call_model must take `trace`"
assert _p["trace"].default is inspect.Parameter.empty, "`trace` must have NO default — that is the enforcement"
assert _p["trace"].kind is inspect.Parameter.KEYWORD_ONLY, "`trace` must be keyword-only"
assert "label" not in _p and "trace_meta" not in _p, \
    "label/trace_meta are superseded by `trace` (one source of truth for console + Langfuse)"
print("required-trace OK (keyword-only, no default; label/trace_meta retired)")

# 3. Stage-vocabulary parity: helpers.STAGES === the stages the call sites actually build.
#    Same lockstep discipline as schemas_v2.BLOCK_TYPES <-> renderBlock: a stage that
#    drifts out of the declared set is either an unvalidated name or dead vocabulary.
_used = set()
for _mod, _tree in _src_trees():
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Call):
            continue
        _fn = _fname(_node)
        if _fn not in _BUILDERS:
            continue
        _i = _STAGE_ARG[_fn]
        if len(_node.args) > _i and isinstance(_node.args[_i], ast.Constant):
            _used.add(_node.args[_i].value)
assert _used <= helpers.STAGES, f"call sites use undeclared stage(s): {sorted(_used - helpers.STAGES)}"
assert helpers.STAGES <= _used, f"helpers.STAGES declares unused stage(s): {sorted(helpers.STAGES - _used)}"
print(f"stage-parity OK ({len(_used)} stages declared === used: {', '.join(sorted(_used))})")

# 4. The contract rejects an unattributable call rather than emitting an anonymous one.
#    (Deterministic + pure, so it fails in dev on the first call, never in Langfuse.)
from types import SimpleNamespace as _NS  # noqa: E402

_spec = _NS(topic_id="ap-chem-1-1-moles", board="AP (College Board)", subject="Chemistry", level="AP")
for _bad, _why in (
    (lambda: helpers.trace_for("notes.nope", "outline", group="g"), "unknown feature"),
    (lambda: helpers.trace_for(helpers.FEATURE_GENERATE, "sectionn", group="g"), "typo'd stage"),
    (lambda: helpers.trace_for(helpers.FEATURE_GENERATE, "outline", group=""), "empty group"),
):
    try:
        _bad()
        raise AssertionError(f"trace_for must reject {_why}")
    except ValueError:
        pass
print("contract-validation OK (unknown feature / typo'd stage / empty group all rejected)")

# 5. Shape: the feature names the TRACE, the stage names the OBSERVATION, and the
#    dimensions land as tags. This is what turns "unknown" into a filterable call.
_t = helpers.trace_topic(_spec, "section", item="Periodic Trends")
assert _t["feature"] == helpers.FEATURE_GENERATE == "notes.generate", "feature -> trace name"
assert _t["observation"] == "notes.section", "stage -> namespaced observation name"
assert _t["group"] == "ap-chem-1-1-moles", "one trace per topic"
assert _t["label"] == "section:Periodic Trends", "console label distinguishes sibling sections"
assert helpers.trace_topic(_spec, "outline")["label"] == "outline:ap-chem-1-1-moles", \
    "no `item` -> the label falls back to the topic"
for _want in ("app:class-notes", "feature:notes.generate", "stage:section",
              "board:AP (College Board)", "subject:Chemistry", "level:AP", "topic:ap-chem-1-1-moles"):
    assert _want in _t["tags"], f"missing filter tag {_want!r} (got {_t['tags']})"
assert _t["metadata"]["topic"] == "ap-chem-1-1-moles" and _t["metadata"]["item"] == "Periodic Trends"
# The observation name must NOT carry the variable part — that is what made the old
# `v2-section:Periodic Trends and Co` names unaggregatable (one name per section).
assert "Periodic" not in _t["observation"], "the variable part belongs in tags/metadata, never the name"
assert helpers.trace_topic(_spec, "section", item="Other")["observation"] == _t["observation"], \
    "sibling sections must share ONE observation name so cost-per-stage aggregates"
print("contract-shape OK (feature->trace, stage->observation, dimensions->tags; names stay aggregatable)")

# 6. Grouping: every stage of one topic rolls up under ONE trace id; a different topic
#    gets a different one. Seeded, so it holds across threads (the pipeline fans out).
_seeds = {helpers.trace_topic(_spec, s)["group"] for s in ("outline", "section", "coverage",
                                                           "practice", "finalize", "image.select",
                                                           "past_papers.verify")}
assert _seeds == {"ap-chem-1-1-moles"}, f"all stages of a topic must share one trace group, got {_seeds}"
_other = _NS(topic_id="ap-chem-1-2-mass-spectra", board="AP (College Board)", subject="Chemistry", level="AP")
assert helpers.trace_topic(_other, "section")["group"] != _t["group"], "different topics -> different traces"
# extract_specs groups by the SUBJECT SPEC run: enumerate + every per-topic extract in one trace.
_e1 = helpers.trace_spec_run("spec.enumerate", board="AP (College Board)", subject="Chemistry")
_e2 = helpers.trace_spec_run("spec.extract", board="AP (College Board)", subject="Chemistry", item="1.1 Moles")
assert _e1["group"] == _e2["group"] == "AP (College Board):Chemistry", "one trace per subject spec run"
assert _e1["feature"] == _e2["feature"] == helpers.FEATURE_EXTRACT != helpers.FEATURE_GENERATE, \
    "extraction is its own feature — its cost must not read as notes generation"
assert _e2["label"] == "spec.extract:1.1 Moles"
print("trace-grouping OK (topic stages share one trace; extract groups per subject spec run)")

# 7. Langfuse DROPS a propagated value that is not a str or exceeds 200 chars — silently.
#    A dropped tag is the same blind spot this whole change removes, so clip at the source.
_long = _NS(topic_id="t" * 400, board="B" * 400, subject="S" * 400, level="AP")
_c = helpers.trace_topic(_long, "section", item="H" * 400, marks=7)
for _k, _v in _c["metadata"].items():
    assert isinstance(_v, str), f"metadata[{_k}] must be a str for propagate_attributes, got {type(_v)}"
    assert len(_v) <= 200, f"metadata[{_k}] is {len(_v)} chars — Langfuse would drop it"
for _tag in _c["tags"]:
    assert isinstance(_tag, str) and len(_tag) <= 200, f"tag {_tag[:40]!r} would be dropped ({len(_tag)} chars)"
assert _c["metadata"]["marks"] == "7", "non-str detail is coerced, not dropped"
print("langfuse-limits OK (values coerced to str + clipped to 200 chars; nothing silently dropped)")

# 8. Tracing NEVER breaks a run: _log_generation swallows a broken client/response. The
#    contract is validated deterministically (above); transmission is best-effort.
helpers._LF = _NS(create_trace_id=lambda **k: (_ for _ in ()).throw(RuntimeError("langfuse is down")))
try:
    helpers._log_generation(_t, "gemini-3.1-pro-preview", _NS(usage_metadata=None))
finally:
    helpers._LF = "uninit"
print("best-effort-logging OK (a broken Langfuse client cannot fail a topic)")

print("\nALL TRACING SMOKE CHECKS PASSED")
