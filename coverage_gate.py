"""Deterministic coverage-gate logic for the v2 pipeline (NO genai import).

The v2 coverage audit (``verify_v2``) is advisory input; THIS module turns it
into an ENFORCED contract: which objectives failed, which section must re-teach
each, and the feedback to hand the re-draft. Kept genai-free (stdlib + typing
only) so the offline smoke (``_smoke_v2.py``) can exercise the gate without a
key or network. All functions are pure and duck-typed over the audit/section
objects (``.code`` / ``.covered`` / ``.gap_note`` / ``.covers_objective_codes``).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only — never imported at runtime
    from schemas import LOCoverage, LearningObjective
    from schemas_v2 import InteractiveSection


class CoverageError(RuntimeError):
    """Raised when notes still fail the objective-coverage contract after the
    allotted regeneration attempts. The pipeline refuses to emit an artifact, so
    nothing under-covered ever reaches ``out/`` (the hard-block the reviewer asked
    for — a false ``covered`` flag must stop the build, not pass through)."""

    def __init__(self, topic_id: str, uncovered: "list[LOCoverage]"):
        self.topic_id = topic_id
        self.uncovered = uncovered
        detail = "; ".join(
            f"[{c.code}] {c.gap_note or 'not taught to depth'}" for c in uncovered
        )
        super().__init__(
            f"{topic_id}: {len(uncovered)} objective(s) not covered after regeneration — {detail}"
        )


def uncovered_items(items: "list[LOCoverage]") -> "list[LOCoverage]":
    """The audit entries marked ``covered == False`` (the gaps to close)."""
    return [c for c in items if not c.covered]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def best_section_for(statement: str, section_texts: list[str]) -> int:
    """Index of the section whose text overlaps ``statement`` most.

    Used ONLY when an uncovered objective is claimed by NO section (an outline
    gap): we still have to route it somewhere to re-teach it. Deterministic
    (first max wins); falls back to section 0.
    """
    want = _tokens(statement)
    best, best_score = 0, -1
    for i, txt in enumerate(section_texts):
        score = len(want & _tokens(txt))
        if score > best_score:
            best, best_score = i, score
    return best


def plan_regeneration(
    uncovered: "list[LOCoverage]",
    sections: "list[InteractiveSection]",
    section_texts: list[str],
    objectives: "list[LearningObjective]",
) -> "tuple[dict[int, list[str]], dict[int, list[str]]]":
    """Map each uncovered objective to the section index that must re-teach it.

    Returns ``(targets, forced)``:
      * ``targets``: section index -> feedback lines to inject into its re-draft.
      * ``forced`` : section index -> objective codes force-added to that section's
        claim list (codes NO section had claimed; routed to the best-overlapping
        section so the re-draft is told to teach them and the next verify can find
        them).
    """
    stmt = {o.code: o.statement for o in objectives}
    claims = [set(s.covers_objective_codes) for s in sections]
    targets: dict[int, list[str]] = {}
    forced: dict[int, list[str]] = {}
    for c in uncovered:
        line = (
            f"  - [{c.code}] {stmt.get(c.code, '')} — Gap: "
            f"{c.gap_note or 'only mentioned in passing; teach it to full depth this time.'}"
        )
        owners = [i for i, claim in enumerate(claims) if c.code in claim]
        if not owners:
            i = best_section_for(stmt.get(c.code, ""), section_texts)
            owners = [i]
            forced.setdefault(i, []).append(c.code)
        for i in owners:
            targets.setdefault(i, []).append(line)
    return targets, forced


def feedback_block(lines: list[str]) -> str:
    """The COVERAGE FIX block injected into a section re-draft (empty -> '')."""
    if not lines:
        return ""
    return (
        "\nCOVERAGE FIX — a previous draft of THIS section did NOT teach the following "
        "objective(s) to the required depth. You MUST teach each one fully and explicitly "
        "this time (a real explanation/worked derivation — not merely a mention in an MCQ "
        "or an aside):\n" + "\n".join(lines) + "\n"
    )


# ---------------------------------------------------------------------------
# Structural coverage evidence
# ---------------------------------------------------------------------------
#
# The model verifier can be wrong: it may mark an objective covered:true when the
# only thing "teaching" it is an MCQ (the LO 7.3 "prove from first principles"
# failure — assessed by a quick-check, never a worked derivation). These
# deterministic rules add a NECESSARY condition the verifier cannot fake: a command
# word that demands a specific teaching artifact must have that artifact present in a
# section that claims the objective. Composes WITH the model verdict (union), never
# replaces it.

# command word -> block types that satisfy it (OR within a set). Unmapped/soft verbs
# (explain/describe/interpret/apply/…) have no distinctive vehicle, so no rule.
_CORE_REQUIREMENTS: "dict[str, frozenset[str]]" = {
    "prove":     frozenset({"step_reveal"}),
    "derive":    frozenset({"step_reveal"}),
    "show that": frozenset({"step_reveal"}),
    "calculate":     frozenset({"numeric", "sim", "step_reveal"}),
    "determine":     frozenset({"numeric", "sim", "step_reveal"}),
    "solve":         frozenset({"numeric", "sim", "step_reveal"}),
    "differentiate": frozenset({"step_reveal", "numeric"}),
}
# define/state/recall -> flip_cards is higher-false-positive (a definition can live in
# prose), so it is gated behind CONFIG["structural_gate_recall"] (default off).
_RECALL_REQUIREMENTS: "dict[str, frozenset[str]]" = {
    "define": frozenset({"flip_cards"}),
    "state":  frozenset({"flip_cards"}),
    "recall": frozenset({"flip_cards"}),
}
# Human phrasing for a missing artifact (drives the re-draft feedback line).
_BLOCK_PHRASING = {
    "step_reveal": "a worked, step-by-step derivation (a step_reveal block)",
    "numeric":     "a numeric question with a mark scheme (a numeric block)",
    "sim":         "an interactive calculation (a sim block)",
    "flip_cards":  "a definition/recall card (a flip_cards block)",
}


def _norm_verb(word: str) -> str:
    """Lowercase + collapse whitespace so 'Show That' == 'show that'."""
    return " ".join((word or "").lower().split())


def required_block_groups(command_words, *, include_recall: bool = False) -> "set[frozenset[str]]":
    """The OR-groups an objective must satisfy given its command words.

    AND across the words (each mapped verb contributes a group that must be hit),
    OR within a group (any one block type satisfies it). Unknown/soft verbs
    contribute nothing; identical groups dedupe (prove + derive -> {step_reveal}).
    Empty result => no structural requirement.
    """
    table = dict(_CORE_REQUIREMENTS)
    if include_recall:
        table.update(_RECALL_REQUIREMENTS)
    groups: "set[frozenset[str]]" = set()
    for w in command_words or []:
        g = table.get(_norm_verb(w))
        if g:
            groups.add(g)
    return groups


def structural_gaps(objectives, sections, *, include_recall: bool = False) -> "dict[str, set[frozenset[str]]]":
    """LO code -> the set of OR-groups still UNsatisfied for it.

    Pure + duck-typed: objectives need ``.code`` / ``.command_words``; sections need
    ``.covers_objective_codes`` and ``.blocks[].type``. Block types are unioned across
    EVERY section that claims the LO (an LO taught across sections is legitimately
    evidenced by the union). Necessary-not-sufficient: it confirms the artifact exists
    in a section teaching the LO, not that the artifact is *about* the LO — so it
    composes with the semantic verifier rather than replacing it.
    """
    present: "dict[str, set[str]]" = {}
    for s in sections:
        types = {getattr(b, "type", "") for b in getattr(s, "blocks", None) or []}
        for code in getattr(s, "covers_objective_codes", None) or []:
            present.setdefault(code, set()).update(types)

    failed: "dict[str, set[frozenset[str]]]" = {}
    for o in objectives:
        groups = required_block_groups(getattr(o, "command_words", None), include_recall=include_recall)
        if not groups:
            continue
        have = present.get(o.code, set())
        missing = {g for g in groups if not (g & have)}
        if missing:
            failed[o.code] = missing
    return failed


def structural_fail_codes(objectives, sections, *, include_recall: bool = False) -> "set[str]":
    """Just the set of LO codes that fail the structural check."""
    return set(structural_gaps(objectives, sections, include_recall=include_recall))


class StructuralGap:
    """An LOCoverage-shaped stand-in for a structural failure (there is no verifier
    object for it). Exposes ``.code`` / ``.gap_note`` / ``.covered`` so it flows
    through the SAME ``uncovered_items`` / ``plan_regeneration`` / ``CoverageError``
    path as a model-reported gap, with no changes to those functions."""
    covered = False

    def __init__(self, code: str, gap_note: str):
        self.code = code
        self.gap_note = gap_note


def _phrase_groups(missing: "set[frozenset[str]]") -> str:
    parts = []
    for g in sorted(missing, key=lambda x: sorted(x)):
        parts.append(" or ".join(_BLOCK_PHRASING.get(t, f"a {t} block") for t in sorted(g)))
    return "; ".join(parts)


def structural_gap_items(objectives, sections, *, include_recall: bool = False) -> "list[StructuralGap]":
    """Structural failures as LOCoverage-like items, in objective order."""
    failed = structural_gaps(objectives, sections, include_recall=include_recall)
    out: "list[StructuralGap]" = []
    for o in objectives:  # stable order
        if o.code in failed:
            out.append(StructuralGap(
                o.code,
                "assessed without the required teaching structure — add "
                + _phrase_groups(failed[o.code])))
    return out
