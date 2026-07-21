"""Offline self-test for the CURRICULUM gate (no key/network).

This gate is the autonomous replacement for the human approval step. Everything it
decides is pure and deterministic, so the whole trust boundary is exercised here without
a key — which is the point: the thing that ACCEPTS a spec must be checkable independently
of the model that PRODUCED it.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))   # app modules live in src/
sys.path.insert(0, os.path.join(_ROOT, "tests"))  # shared fixture builder

import batch  # noqa: E402
import pdf_text  # noqa: E402
import spec_gate as sg  # noqa: E402
from _pdfgen import make_pdf  # noqa: E402

# A stand-in "spec page": real codes, real statements, long enough that a quote drawn
# from it clears the evidence floor.
_SPEC_LINES = [
    "Topic 8: Energetics I",
    "8.1 be able to define the standard enthalpy change of formation of a compound",
    "understand that the standard enthalpy change of formation of an element is zero by definition",
    "8.2 be able to construct Hess cycles to calculate an unknown enthalpy change from given data",
    "students should be able to apply Hess's law to find enthalpy changes that cannot be measured",
]
_PAPER = pdf_text.extract_text(make_pdf(_SPEC_LINES))


def _spec(objectives, topic="Energetics"):
    return {"topic": topic, "learning_objectives": objectives}


_GOOD = _spec([
    {"code": "8.1", "statement": "define standard enthalpy change of formation",
     "evidence_quote": "be able to define the standard enthalpy change of formation of a compound"},
    {"code": "8.2", "statement": "construct Hess cycles",
     "evidence_quote": "be able to construct Hess cycles to calculate an unknown enthalpy change"},
])

# 1. A spec whose every code AND evidence quote is in the source PDF is approvable.
assert sg.spec_gaps(_GOOD, _PAPER) == [], "a fully grounded spec has no gaps"
assert sg.plan_spec_decision([], 0, 2) == "approve", "no gaps means approve"
print("grounded spec OK (codes + quotes both located -> approvable)")

# 2. A FABRICATED code cannot be approved. This is the check a model cannot talk its way
#    past: the string is either in the fetched document or it is not.
_bad_code = _spec([dict(_GOOD["learning_objectives"][0]),
                   {"code": "8.99", "statement": "invented",
                    "evidence_quote": "be able to construct Hess cycles to calculate an unknown enthalpy change"}])
_g = sg.code_evidence_gaps(_bad_code, _PAPER)
assert len(_g) == 1 and _g[0].code == "8.99" and _g[0].kind == sg.GAP_CODE_ABSENT, \
    "a code absent from the spec PDF is a gap"
#    Codes fold across punctuation: boards print the same point several ways.
_dotted = _spec([{"code": "8 . 1", "statement": "s", "evidence_quote": _GOOD["learning_objectives"][0]["evidence_quote"]}])
assert sg.code_evidence_gaps(_dotted, _PAPER) == [], "'8 . 1' matches '8.1' through normalise_code"
#    PIN THE WEAKNESS: a SHORT code is a substring-of-everything and proves nothing. Here
#    "8" is "located" purely because it starts "8.1". At real scale this is much worse —
#    measured against the 238-page AP Chemistry CED (285,889 chars), structured codes
#    discriminated correctly (all 28 real ones found, 'SAP-9.Z' / 'TRA-99.A' absent) but
#    bare numeric ones collided by chance: '1.1' folds to '11' and was FOUND, as were
#    '9.9' and '12.4'. Edexcel ('8.1') and IGCSE ('6.7S') print exactly that format.
_short = _spec([{"code": "8", "statement": "not a real code",
                 "evidence_quote": _GOOD["learning_objectives"][0]["evidence_quote"]}])
assert sg.code_evidence_gaps(_short, _PAPER) == [], \
    "a short numeric code collides by chance - the code check alone cannot carry a board like Edexcel"
#    Which is why the QUOTE carries the proof: keep the colliding code, fabricate the
#    quote, and the spec still dies.
_short["learning_objectives"][0]["evidence_quote"] = ("candidates must memorise every standard "
                                                     "enthalpy of atomisation printed in the data booklet")
assert sg.spec_gaps(_short, _PAPER), "a fabricated quote is caught even when the code collides"
print("code evidence OK (structured codes discriminate; short ones collide - quote carries the proof)")

# 3. A real code with a FABRICATED quote is still rejected — the code alone is a short
#    token that can collide by chance, the quote is what actually proves the reading.
_bad_quote = _spec([{"code": "8.1", "statement": "define enthalpy of formation",
                     "evidence_quote": "candidates must memorise the four standard enthalpies of "
                                       "atomisation listed in the data booklet"}])
_g = sg.quote_evidence_gaps(_bad_quote, _PAPER)
assert len(_g) == 1 and _g[0].kind == sg.GAP_QUOTE_ABSENT, "an unprovable quote is a gap"
_no_quote = _spec([{"code": "8.1", "statement": "define", "evidence_quote": ""}])
assert sg.quote_evidence_gaps(_no_quote, _PAPER)[0].kind == sg.GAP_NO_QUOTE, \
    "an objective with no quote at all cannot be traced"
print("quote evidence OK (fabricated + missing quotes both rejected)")

# 4. NO TEXT LAYER => approve nothing. A scanned syllabus would otherwise silently
#    downgrade the strongest check to "the model said so", which is the hole this closes.
_g = sg.spec_gaps(_GOOD, None)
assert _g and _g[0].kind == sg.GAP_NO_TEXT, "an unreadable spec PDF blocks approval outright"
assert sg.plan_spec_decision(_g, 0, 2) != "approve", "nothing is approvable without source text"
print("no-text policy OK (unreadable spec PDF cannot approve anything)")

# 5. Shape gaps: the PARTIAL backstop against silent under-extraction. The default floor
#    is 1 — "extracted nothing at all" — and that restraint is the point. It was briefly 2,
#    on the assumption that a one-objective topic meant a mis-sliced PDF. Wrong for AP: the
#    CED prints exactly ONE "LEARNING OBJECTIVE" per topic (1.1.A) with several "ESSENTIAL
#    KNOWLEDGE" points under it (1.1.A.1/.2/.3), which belong in depth_profile. At 2 this
#    flagged 53 CORRECT specs, and the repair loop "fixed" them by promoting Essential
#    Knowledge into learning_objectives — a schema violation that looks like an improvement
#    in a diff. Pin the restraint so nobody re-raises it without the board's structure.
assert sg.shape_gaps(_spec([])) and sg.shape_gaps(_spec([]))[0].kind == sg.GAP_TOO_FEW, \
    "extracting nothing at all is always a failure"
assert sg.shape_gaps(_spec([_GOOD["learning_objectives"][0]])) == [], \
    "a ONE-objective topic is legitimate (AP prints exactly one LEARNING OBJECTIVE per topic)"
assert sg.shape_gaps(_GOOD) == [], "two objectives is equally fine"
assert sg.shape_gaps(_GOOD, min_objectives=5), "the floor is configurable for a board that needs it"
print("shape gaps OK (catches empty extraction; a 1-objective AP topic is NOT a failure)")

# 6. The repair loop. Boundary is `attempt >= max_retries` -> block, matching
#    enforce_coverage_v2 — at attempt == max_retries the budget is SPENT.
_g = sg.spec_gaps(_bad_code, _PAPER)
assert sg.plan_spec_decision(_g, 0, 2) == "repair", "first failure repairs"
assert sg.plan_spec_decision(_g, 1, 2) == "repair", "second failure repairs"
assert sg.plan_spec_decision(_g, 2, 2) == "block", "at the cap it blocks rather than retrying again"
assert sg.plan_spec_decision(_g, 0, 0) == "block", "a zero budget blocks immediately"
#    A repair must see DIFFERENT evidence, or it re-asks the same question of the same
#    pages — a self-CONFIRMATION loop, not a self-repair one.
_kw = sg.repair_keywords(_bad_code, _g)
assert "8.99" in _kw and "Energetics" in _kw, "the next slice is steered at what failed"
assert len(_kw) == len(set(_kw)), "keywords are de-duplicated"
print("repair loop OK (>= cap blocks; retry re-slices on the failing codes)")

# 7. Feedback injection mirrors coverage_gate: empty in -> empty out, so the FIRST-pass
#    prompt stays byte-identical to what it was before this gate existed.
assert sg.spec_feedback_block([]) == "", "no gaps means no injected block"
_fb = sg.spec_feedback_block(sg.spec_feedback_lines(_g))
assert "SPEC FIX" in _fb and "8.99" in _fb, "the fix block names the failing code"
assert "OMIT it rather than inventing" in _fb, "the redraft is told omission beats invention"
print("feedback block OK (empty-safe; names the failing codes)")

# 8. Evidence quotes must NEVER persist: they are verbatim text from a copyrighted
#    specification, and curriculum/ is git-tracked. Same reason VerifiedPaper carries no
#    evidence field.
_stripped = sg.strip_evidence(_GOOD)
assert all("evidence_quote" not in lo for lo in _stripped["learning_objectives"]), \
    "evidence quotes are stripped before a spec is written"
assert all("evidence_quote" in lo for lo in _GOOD["learning_objectives"]), \
    "stripping returns a copy and does not mutate the caller's dict"
assert _stripped["learning_objectives"][0]["code"] == "8.1", "everything else survives the strip"
print("evidence strip OK (no copyrighted spec text reaches curriculum/)")

# 9. The UNVERIFIED marker is self-describing, and rewriting a reason can NEVER clear the
#    gate — that would be a silent downgrade from "will not generate" to "ships ungrounded".
_reason = sg.block_reason(sg.spec_gaps(_bad_code, _PAPER))
assert "code-absent" in _reason and "8.99" in _reason, "the reason names what failed"
_src = batch.set_unverified_reason("Auto-extracted from Edexcel Issue 3 — UNVERIFIED: old", _reason)
assert batch.UNVERIFIED_MARKER in _src, "rewriting a reason keeps the gate token"


class _S:
    source = _src


assert batch.is_unverified(_S()), "a spec with a rewritten reason is still gated"
assert "Edexcel Issue 3" in _src, "the origin survives the rewrite"
assert not batch.is_unverified(type("X", (), {"source": batch.clear_unverified_marker(_src)})()), \
    "clearing the marker is what makes a spec generatable"
assert "verified against" in sg.approve_note("Edexcel Issue 3", 7).lower(), "the approve note states the basis"
print("marker semantics OK (self-describing; a reason rewrite cannot clear the gate)")

print("\nALL SPEC-GATE SMOKE CHECKS PASSED")
