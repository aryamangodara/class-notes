"""Deterministic, genai-free decision logic for the CURRICULUM gate.

This is the autonomous replacement for the human approval step. It is the third
enforcement tier, above the two that already exist:

    curriculum-time (HERE)  a spec whose codes and statements cannot be traced to the
                            official PDF stays UNVERIFIED, and an UNVERIFIED spec does
                            not generate. Failure mode: ungenerable, not wrong.
    generation-time         coverage + block completeness (coverage_gate / render_v2).
                            Failure mode: nothing written.

WHAT REPLACED THE HUMAN, AND WHY IT IS STRONGER. The old flow stamped every extraction
UNVERIFIED and waited for someone to read a `git diff` before `approve_specs.py` cleared
it. In practice `deploy/run_all.sh` ran `approve_specs --apply` unconditionally as its own
phase 3 — so the marker was wiped with nothing checking anything, and 0 of 103 specs were
unverified. That is a rubber stamp, not a gate.

The replacement asks a question a model cannot talk its way past: **is this string in the
document we fetched?** Every approved code must appear in the spec PDF's extracted text,
and every objective must carry a verbatim quote provably copied out of it
(``pdf_text.quote_supported`` — the same primitive that gates past-paper citations, one
proof mechanism with two consumers). Anything short of that re-extracts with the gaps
injected, then stays UNVERIFIED.

SEPARATION OF POWERS SURVIVES, DIFFERENTLY. The old gate's real value was never "a human
looked" — it was that the thing PRODUCING a claim is not the thing ACCEPTING it. Here the
producer is a model and the acceptor is a pure function over fetched bytes, which no model
reasoning can influence. That is why this module is genai-free and lives apart from
``extract_specs``: the writer never approves itself.

HONEST LIMIT — necessary, not sufficient (same framing as ``coverage_gate.structural_gaps``).
Proving a code and a quote are in the document does NOT prove the code belongs to THIS
topic, nor that the statement faithfully summarises it, nor that nothing was silently
omitted. Under-extraction in particular is invisible here; see ``shape_gaps``.
"""
from __future__ import annotations

import pdf_text

# What went wrong, in the vocabulary the feedback block and the UNVERIFIED reason share.
GAP_CODE_ABSENT = "code-absent"
GAP_QUOTE_ABSENT = "quote-absent"
GAP_NO_QUOTE = "no-quote"
GAP_TOO_FEW = "too-few-objectives"
GAP_NO_TEXT = "no-pdf-text"


class SpecGap:
    """One reason a spec cannot be approved, shaped like an ``LOCoverage`` so it can flow
    through the same feedback machinery the coverage gate uses."""

    __slots__ = ("kind", "code", "statement", "detail")

    def __init__(self, kind: str, code: str, statement: str, detail: str):
        self.kind, self.code, self.statement, self.detail = kind, code, statement, detail

    def __repr__(self):  # pragma: no cover - debug aid
        return f"SpecGap({self.kind}, {self.code!r})"


def _objectives(spec_dict: dict) -> "list[dict]":
    return list(spec_dict.get("learning_objectives") or [])


def code_evidence_gaps(spec_dict: dict, paper) -> "list[SpecGap]":
    """Codes that do not appear in the spec PDF's own text.

    Folded through ``normalise_code`` so '1.7.A.1' matches '1.7 A 1' and '1.7a1' — boards
    print the same code with different punctuation and the extractor copies whichever it
    saw. A code the document does not contain is one the model invented or misread.

    STRENGTH IS FORMAT-DEPENDENT, and measured against the real 238-page AP Chemistry CED
    (285,889 extracted chars):
      * STRUCTURED codes discriminate well — all 28 real codes in the shipped AP Chem
        specs were located, and invented ones ('SAP-9.Z', 'TRA-99.A', 'SPQ-1.ZZ') were
        all correctly absent.
      * BARE NUMERIC codes prove almost nothing. '1.1' folds to the skeleton '11', which
        occurs by chance all over a long document — measured FOUND, as were '9.9' and
        '12.4'. That is precisely the format Edexcel A-Level ('8.1') and IGCSE ('6.7S')
        print, so on those boards this check is a cheap filter and NOT a proof.

    This is why ``quote_evidence_gaps`` runs alongside it and carries the real weight: a
    40-character contiguous verbatim run cannot collide by chance the way a two-digit
    token can. Do not "strengthen" this by requiring longer codes — that would simply mark
    every Edexcel and IGCSE code absent and block those boards entirely.
    """
    if paper is None:
        return [SpecGap(GAP_NO_TEXT, "", "", "the spec PDF yielded no extractable text, so "
                                              "nothing in this spec can be verified against it")]
    out = []
    for lo in _objectives(spec_dict):
        code = (lo.get("code") or "").strip()
        if not code:
            continue
        if pdf_text.normalise_code(code) not in paper.flat:
            out.append(SpecGap(GAP_CODE_ABSENT, code, lo.get("statement", ""),
                               "this code does not appear anywhere in the official spec PDF"))
    return out


def quote_evidence_gaps(spec_dict: dict, paper) -> "list[SpecGap]":
    """Objectives whose verbatim evidence quote is not provably in the fetched PDF.

    This is the check that makes an approval mean something. A code alone is a short token
    that can collide by chance; a quote is a long contiguous run that fabrication cannot
    manufacture against the specific document fetched this run.
    """
    if paper is None:
        return []          # already reported once by code_evidence_gaps
    out = []
    for lo in _objectives(spec_dict):
        code = (lo.get("code") or "").strip()
        quote = (lo.get("evidence_quote") or "").strip()
        if not quote:
            out.append(SpecGap(GAP_NO_QUOTE, code, lo.get("statement", ""),
                               "no evidence quote was supplied, so this objective cannot be "
                               "traced to the spec"))
            continue
        ok, why = pdf_text.quote_supported(quote, paper)
        if not ok:
            out.append(SpecGap(GAP_QUOTE_ABSENT, code, lo.get("statement", ""),
                               f"the evidence quote is not in the spec PDF ({why})"))
    return out


def shape_gaps(spec_dict: dict, *, min_objectives: int = 1) -> "list[SpecGap]":
    """Deterministic shape checks that need neither a model nor the PDF.

    The partial backstop against SILENT UNDER-EXTRACTION — the worst hole left in the
    autonomous flow, because if extraction returns 4 of 9 objectives every downstream gate
    passes at 100%, the notes look complete, and nothing compares the count to the source.

    It is only PARTIAL, and the floor defaults to 1 (i.e. "extracted nothing at all") for a
    reason worth knowing before you raise it. A one-objective topic is NOT evidence of a
    mis-sliced PDF on every board: the AP CED prints exactly ONE "LEARNING OBJECTIVE" per
    topic with several "ESSENTIAL KNOWLEDGE" points beneath it, so 1 is the correct and
    expected count there. At a floor of 2 this gate flagged 53 correct AP specs and the
    repair loop "fixed" them by promoting Essential Knowledge into ``learning_objectives``
    — a schema violation that reads as an improvement in a diff. Any real floor has to come
    from the board's published structure, curated like ``BOARD_EXAM_TIPS``, not guessed.
    """
    n = len(_objectives(spec_dict))
    if n < min_objectives:
        return [SpecGap(GAP_TOO_FEW, "", "",
                        f"only {n} learning objective(s) were extracted (expected at least "
                        f"{min_objectives}) - the spec pages selected probably missed the topic")]
    return []


def spec_gaps(spec_dict: dict, paper, *, min_objectives: int = 2) -> "list[SpecGap]":
    """Every reason this spec cannot be approved, cheapest check first."""
    return (shape_gaps(spec_dict, min_objectives=min_objectives)
            + code_evidence_gaps(spec_dict, paper)
            + quote_evidence_gaps(spec_dict, paper))


def plan_spec_decision(gaps, attempt: int, max_retries: int) -> str:
    """-> 'approve' | 'repair' | 'block'.

    ``attempt`` is how many re-extractions have ALREADY happened. The boundary is ``>=``,
    matching ``enforce_coverage_v2`` — at ``attempt == max_retries`` the budget is spent
    and the answer is block, not one more try.
    """
    if not gaps:
        return "approve"
    return "repair" if attempt < max_retries else "block"


def spec_feedback_lines(gaps) -> "list[str]":
    """'  - [CODE] statement — Gap: why' per gap, for injection into a re-extraction."""
    out = []
    for g in gaps:
        head = f"[{g.code}] " if g.code else ""
        stmt = (g.statement or "").strip()
        out.append(f"  - {head}{stmt[:90]} — Gap: {g.detail}")
    return out


def spec_feedback_block(lines: "list[str]") -> str:
    """The SPEC FIX block injected into a re-extraction (empty -> '', so the first-pass
    prompt is byte-identical to today's). Sibling of ``coverage_gate.feedback_block``."""
    if not lines:
        return ""
    return ("\nSPEC FIX — a previous extraction of THIS topic produced item(s) that could NOT be "
            "located in the attached specification pages. Re-extract from the attached pages and "
            "fix each: quote the code and the wording EXACTLY as printed, and give an "
            "`evidence_quote` copied character-for-character from the page. If a listed item "
            "genuinely does not belong to this topic, OMIT it rather than inventing a code:\n"
            + "\n".join(lines) + "\n")


def repair_keywords(spec_dict: dict, gaps) -> "list[str]":
    """Extra slice keywords for a re-extraction, drawn from what FAILED.

    Load-bearing: without this, attempt 2 re-slices to the same pages and asks the same
    question of the same evidence, which is a self-CONFIRMATION loop rather than a
    self-repair one. Since the dominant cause of an absent code is a mis-sliced CED, the
    fix is to steer the next slice at the codes that could not be found.
    """
    out = []
    for g in gaps:
        if g.code:
            out.append(g.code)
    topic = (spec_dict.get("topic") or "").strip()
    if topic:
        out.append(topic)
    return list(dict.fromkeys(out))


def approve_note(citation: str, n_objectives: int) -> str:
    """The provenance note replacing the UNVERIFIED clause on approval. Deterministic (no
    date) so the offline smoke can assert it."""
    return (f"Auto-approved: {n_objectives} objective(s) verified against {citation} "
            f"(every code and evidence quote located in the source PDF)")


def block_reason(gaps) -> str:
    """A one-line, machine-written reason for the UNVERIFIED clause, so `--list` and
    `git diff` say WHAT failed without anyone opening a report file."""
    if not gaps:
        return "unknown"
    kinds: "dict[str, int]" = {}
    for g in gaps:
        kinds[g.kind] = kinds.get(g.kind, 0) + 1
    parts = [f"{v}x {k}" for k, v in sorted(kinds.items())]
    codes = [g.code for g in gaps if g.code][:4]
    tail = f" ({', '.join(codes)})" if codes else ""
    return "; ".join(parts) + tail


def strip_evidence(spec_dict: dict) -> dict:
    """Remove every extraction-time ``evidence_quote`` before the spec is written.

    The quotes are verbatim text from a copyrighted specification. They exist ONLY to be
    checked against the PDF in the same run that produced them, and persisting them would
    ship syllabus text into a git-tracked file — the same reason ``VerifiedPaper`` carries
    no evidence field. Returns a new dict; the caller writes this, not the original.
    """
    d = dict(spec_dict)
    d["learning_objectives"] = [{k: v for k, v in lo.items() if k != "evidence_quote"}
                                for lo in _objectives(spec_dict)]
    return d
