"""Offline self-test for deterministic PDF-text grounding (no key/network).

This is the module that turns "verified" from *a second model agreed* into *the bytes are
in the document we fetched*. Every check below pins one property that a fabricated
citation would otherwise slip through, so treat a failure here as a grounding regression,
not a formatting nit.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))   # app modules live in src/
sys.path.insert(0, os.path.join(_ROOT, "tests"))  # shared fixture builder

import pdf_text as P  # noqa: E402
from _pdfgen import PAPER_LINES, make_pdf  # noqa: E402

# 1. normalise folds every way a PDF's own text and a model's copy of it can diverge.
#    Each pair is a real failure class: if any stops folding, genuine citations start
#    getting dropped for typography rather than for being wrong.
for _a, _b, _why in [
    ("the ﬁrst ﬂuid", "the first fluid", "ligatures"),
    ("738 kJ mol⁻¹", "738 kJ mol-1", "superscripts"),
    ("H₂SO₄ at 25°C", "H2SO4 at 25 C", "subscripts"),
    ("magne­sium", "magnesium", "soft hyphen"),
    ("magne-\nsium oxide", "magnesium oxide", "line-break hyphenation"),
    ("i o n i s a t i o n", "ionisation", "expanded intra-token whitespace"),
    ("the  first\n\n ionisation", "the first ionisation", "collapsed whitespace"),
    ("“Explain,” she’d", '"Explain," she\'d', "curly quotes"),
    ("ΔH = −1.5", "∆H = -1.5", "delta confusable + unicode minus"),
    ("738 kJ mol", "738 kJ mol", "non-breaking space"),
]:
    assert P.normalise(_a) == P.normalise(_b), f"{_why} must fold to one skeleton"
assert P.normalise("") == "" and P.normalise(None) == "", "empty input folds to empty"
assert P.normalise_code("1.7.A.1") == P.normalise_code("1.7 A 1") == P.normalise_code("1.7a1"), \
    "spec codes fold across punctuation and spacing"
print("normalise OK (10 PDF/model divergences + code punctuation fold to one skeleton)")

# 2. extract_text FAILS CLOSED on everything it cannot read. Degrading OPEN here would
#    restore the exact hole this module exists to close, so all three must yield None.
_PAPER = P.extract_text(make_pdf(PAPER_LINES))
assert _PAPER is not None, "a real text layer is usable"
assert _PAPER.pages == 1 and len(_PAPER.lines) == len(PAPER_LINES), "lines kept with page numbers"
assert P.extract_text(b"%PDF-1.4 stub bytes") is None, "unparseable bytes prove nothing"
assert P.extract_text(make_pdf([])) is None, "a PDF with no text layer (a scan) proves nothing"
assert P.extract_text(b"") is None, "empty bytes prove nothing"
print(f"extract_text OK (fails closed on unparseable/scanned/empty; {len(_PAPER.flat)} chars read)")

# 3. Contiguity is the discriminator. The two error modes push a SIMILARITY ratio in
#    opposite directions — padding a real quote lowers it, stitching a fake from the
#    paper's own words raises it — so no ratio threshold separates them. A long verbatim
#    run does: fabrication cannot manufacture one against the PDF fetched this run.
assert P.quote_supported(
    "Explain why the second ionisation energy of magnesium is larger than the first",
    _PAPER)[0], "a verbatim quote is supported"
assert P.quote_supported(
    "In part 3(b)(ii) the candidate must Explain why the second ionisation energy of "
    "magnesium is larger than the first, per the mark scheme", _PAPER)[0], \
    "a real quote wrapped in the model's own framing still survives"
assert P.quote_supported(
    "State Hess's law and explain why it follows from conservation of energy", _PAPER)[0], \
    "a straight apostrophe matches the typographic one the PDF actually renders"
assert not P.quote_supported(
    "Calculate the ionisation energy of magnesium using the mean bond enthalpy table below",
    _PAPER)[0], "a fake stitched from the paper's own vocabulary is rejected"
assert not P.quote_supported(
    "The four elements listed in the table have first ionisation energies of 738, 1451, "
    "7733 and 10540", _PAPER)[0], "wholly invented content is rejected"
assert not P.quote_supported("ionisation energy", _PAPER)[0], \
    "a quote too short to prove anything is rejected"
assert not P.quote_supported("a" * 80, _PAPER)[0], "an unrelated long string is rejected"
assert not P.quote_supported("Explain why the second ionisation energy", None)[0], \
    "no extractable text means nothing can be supported"
print("quote_supported OK (padded-real passes; stitched/invented/short/no-text rejected)")

# 4. The documented LIMIT, pinned deliberately. quote_supported proves a verbatim ANCHOR
#    is present, NOT that every word is: an edit inside a long quote survives when either
#    side still clears the floor. Asserting the true behaviour stops a future reader from
#    trusting a guarantee the code does not make — the identity probe in 5 is the
#    complement that covers the other half.
assert P.quote_supported(
    "Explain why the THIRD ionisation energy of magnesium is larger than the first",
    _PAPER)[0], "a one-word edit inside a long quote still passes — anchor, not whole-quote"
print("quote_supported LIMIT pinned (verbatim anchor, not whole-quote fidelity)")

# 5. locate_question walks the two-level structure real papers actually print. A citation
#    NEVER appears as one "3(b)(ii)" token: AP renders "3." then "(b)" then "(ii)" on
#    separate lines, Edexcel opens with a tab. Measured on the live corpus, a naive
#    line-prefix probe was wrong in BOTH directions on 3 of 5 GENUINE citations — it
#    called real sub-part citations absent, and matched "Q1(a)" against "1 atmosphere
#    equals 760..." on a formula sheet. Hence the walk, and hence advisory-only verdicts.
assert P.question_ref_tokens("3(b)(ii)") == ["3", "b", "ii"], "a citation splits into its parts"
assert P.question_ref_tokens("Q6(d)") == ["6", "d"] and P.question_ref_tokens("Q5") == ["5"]
for _ref in ["3(b)(ii)", "Q3(b)(ii)", "3 (b) (ii)", "1(a)", "Q1(b)", "3(a)"]:
    assert P.locate_question(_ref, _PAPER) == 1, f"{_ref} resolves through the structure walk"
assert P.locate_question("Q6(d)", _PAPER) == 1, "the Edexcel tab convention resolves too"
#    The negative that matters: question 2 EXISTS but has no sub-part (c).
assert P.locate_question("2(c)(i)", _PAPER) is None, \
    "a sub-part that does not exist is not located, even though its question number does"
assert P.locate_question("3(b)(iv)", _PAPER) is None, "a missing sub-sub-part is not located"
for _ref in ["Q9", "null", "", "Q"]:
    assert P.locate_question(_ref, _PAPER) is None, f"{_ref!r} locates nothing"
assert P.locate_question("1(a)", None) is None, "no text means nothing is located"
print("locate_question OK (AP period + Edexcel tab conventions; absent sub-parts rejected)")

# 6. longest_common_run edge cases — it backs every judgement above.
assert P.longest_common_run("abcdef", "zzabcdzz") == 4, "finds the longest contiguous run"
assert P.longest_common_run("", "abc") == 0 and P.longest_common_run("abc", "") == 0, \
    "empty input runs to zero rather than raising"
assert P.longest_common_run("abc", "abc") == 3, "an exact match runs the whole length"
assert P.longest_common_run("xyz", "abc") == 0, "no overlap runs to zero"
print("longest_common_run OK (contiguous, empty-safe, exact)")

print("\nALL PDF-TEXT SMOKE CHECKS PASSED")
