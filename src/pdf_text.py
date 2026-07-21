"""Deterministic PDF-text grounding — the primitive that makes "verified" mean PARSED.

A citation stamped `verified` used to mean only *"a second model agreed"*: both passes of
the past-paper stage are `call_model` calls, and the sole gate was a boolean the verifier
produced. Model-checking-model fails in the SAME direction, which is how a fabricated
`June 2024 · Paper 1 · Q2(c)(i)` — with an invented four-element ionisation-energy table —
got stamped verified when the real Q2 is a single 1-mark MCQ with no sub-parts.

This module is the deterministic half. It extracts the text of the PDF we actually fetched
this run and answers ONE question with no model in the loop: *is this snippet provably
copied out of that document?* Nothing here has an opinion; it either finds the bytes or it
does not.

Design notes that are load-bearing (see the smoke test for each):

* **Fails CLOSED, always.** Missing PyMuPDF, unparseable bytes, or a scanned PDF with no
  text layer all yield ``None`` — and a caller holding ``None`` must publish ZERO
  citations. Cannot prove => do not claim. Degrading OPEN would restore the exact hole
  this module exists to close.
* **Contiguity, not similarity.** See ``quote_supported``.
* **A leaf.** stdlib + a soft ``fitz`` import, nothing from this app. ``ground_specs``
  already imports ``safe_http_url`` FROM ``past_papers``, so putting PDF text handling in
  either of those would close an import cycle the moment the other needed it.
"""
from __future__ import annotations

import re
import unicodedata

# Soft hyphen + the zero-width family: invisible in the rendered PDF, poison for a match.
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

MIN_QUOTE_CHARS = 24   # normalised; below this a quote proves nothing ("energy" is 6)
MIN_RUN_CHARS = 40     # normalised contiguous verbatim overlap required (~8 words)
MIN_DOC_CHARS = 200    # normalised, whole document; below this there is no text layer

_FITZ_WARNED = False


def normalise(text: str) -> str:
    """Fold PDF-extracted text and a model-copied quote onto ONE comparable skeleton.

    NFKC does the heavy lifting: it folds the ligatures (fi -> fi, fl -> fl), the
    superscripts/subscripts (kJ mol-1, H2SO4), the fullwidth forms, and the NBSP / thin /
    figure spaces down to a plain space. Stripping the invisibles kills soft hyphens and
    zero-width joiners.

    Then we delete EVERY non-[a-z0-9] character, which retires the whole remaining
    catalogue in one move:
      * curly vs straight quotes, unicode minus vs hyphen vs en/em dash, and the
        U+0394 GREEK DELTA vs U+2206 INCREMENT confusable -> all become nothing;
      * line-break hyphenation ("magne-\\nsium" -> "magnesium") -> free;
      * BOTH collapsed whitespace ("the  first\\n\\n ionisation") and EXPANDED
        intra-token whitespace ("i o n i s a t i o n", which two-column reflow and
        per-glyph positioning both produce) -> free. A whitespace-COLLAPSING normaliser
        fixes only the first of those two, which is why this deletes instead.

    Cost: Greek letters and symbols contribute nothing ("\\(\\Delta H\\) = -1.5" -> "h15").
    That is deliberate and self-protecting — a formula-only quote collapses below
    MIN_QUOTE_CHARS and is rejected as unprovable, which is the correct posture.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).translate(_INVISIBLE).casefold()
    return _NON_ALNUM.sub("", s)


def normalise_code(code: str) -> str:
    """Skeleton of a spec/LO code, so '1.7.A.1' / '1.7 A 1' / '1.7a1' all compare equal.
    Same folding as ``normalise``; named separately because the CALLER's intent differs
    (an identity token, not a prose snippet) and the spec gate reads better for it."""
    return normalise(code)


def longest_common_run(needle: str, haystack: str) -> int:
    """Length of the longest CONTIGUOUS substring of ``needle`` that occurs in ``haystack``.

    Binary-searched on window length — the property is monotone in k, so each probe is a
    handful of C-level ``str.__contains__`` calls rather than a Python-level scan.
    """
    if not needle or not haystack:
        return 0
    lo, hi = 0, len(needle)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if any(needle[i:i + mid] in haystack for i in range(len(needle) - mid + 1)):
            lo = mid
        else:
            hi = mid - 1
    return lo


class PaperText:
    """The extracted text of ONE fetched PDF.

    ``flat`` is the normalised match haystack; ``lines`` keeps ``(page_no, raw_line)`` so
    an identity probe can anchor on a left-margin question number.
    """
    __slots__ = ("flat", "lines", "pages")

    def __init__(self, flat: str, lines, pages: int) -> None:
        self.flat, self.lines, self.pages = flat, tuple(lines), pages


def quote_supported(quote: str, paper: "PaperText | None") -> "tuple[bool, str]":
    """Is this evidence quote PROVABLY copied out of this PDF? -> (ok, reason).

    Rule:  ``len(q) >= MIN_QUOTE_CHARS  AND  run >= min(MIN_RUN_CHARS, len(q))``

    WHY CONTIGUITY RATHER THAN A SIMILARITY RATIO. The two error modes push a ratio in
    OPPOSITE directions, so no threshold separates them:

      * a real quote the model wrapped in its own framing ("In part (c)(i) the first
        ionisation energy of magnesium is 738 kJ, per the data booklet") scores LOW on
        similarity — padding raises dissimilarity — but keeps a long verbatim core;
      * a fake stitched together from the paper's own vocabulary scores HIGH on
        similarity — the words really are all there — while having no verbatim core.

    Any ratio threshold admitting the first also admits the second. Contiguity is the axis
    that separates them: fabrication cannot manufacture a 40-character verbatim run
    against THE SPECIFIC PDF FETCHED THIS RUN, and padding cannot destroy one. That makes
    this a proof, not a tuned heuristic.

    It degenerates to exact containment for a short quote (24-39 chars) while tolerating
    mangled edges on a long one — one rule, two constants, no knob whose failure is silent.

    HONEST LIMIT — necessary, not sufficient (same framing as ``coverage_gate``'s
    structural rules). This proves a substantial verbatim ANCHOR from this PDF is present.
    It does NOT prove every word of the quote is. A model that copies a real sentence and
    alters one word INSIDE it still passes whenever either side of the edit clears the
    floor — measured, "...THIRD ionisation energy of magnesium is larger than the first"
    survives on a 48-char tail. Raising the bar to a coverage FRACTION does not fix it and
    costs more: a legitimately padded quote scores 61% while that edited one scores 74%,
    so the fraction ranks them backwards. The complement is the offline audit's
    ``question_ref_pages`` identity probe — content grounding catches invented CONTENT,
    identity grounding catches invented QUESTION NUMBERS, and neither alone catches both.
    """
    if paper is None:
        return False, "no extractable PDF text to check against"
    q = normalise(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return False, f"quote too short to prove anything ({len(q)} normalised chars)"
    run = longest_common_run(q, paper.flat)
    need = min(MIN_RUN_CHARS, len(q))
    if run < need:
        return False, f"longest verbatim run {run} < {need} required"
    return True, f"verbatim run {run}/{len(q)}"


# A real question opener, in the two conventions the registry's boards actually print:
#   AP FRQ      "3. Sterling silver is an alloy..."   number + period/paren
#   Edexcel     "6\t Benzoic acid is a weak acid..."  number + TAB (a layout tab)
# The trailing \S is load-bearing twice over: it rejects the bare page-number lines
# ("9", "10", "11") that Edexcel prints in the margin, and it rejects a stray "144" from a
# data table — an earlier looser pattern matched those and terminated a question's span
# early, reporting genuinely present questions as absent.
_Q_START = re.compile(r"^\s*(\d{1,2})(?:[.)]\s+|\t\s*)\S")


def _sub_part(token: str) -> "re.Pattern":
    """A sub-part printed on its OWN line: '(b) The following table...', '(ii) Explain...'."""
    return re.compile(rf"^\s*\(\s*{re.escape(token)}\s*\)", re.I)


def question_ref_tokens(ref: str) -> "list[str]":
    """'3(b)(ii)' / 'Q3 (b) (ii)' / 'Q6(d)' -> ['3','b','ii'] / ['6','d']."""
    return [t for t in re.findall(r"[0-9]+|[a-zA-Z]+", (ref or "").strip().lstrip("Qq")) if t]


def _opener_number(line: str) -> "str | None":
    """The question number this line opens, or None if it is not a question opener."""
    m = _Q_START.match(line)
    return m.group(1) if m else None


def locate_question(ref: str, paper: "PaperText | None") -> "int | None":
    """Page a question reference resolves to, or ``None`` if it cannot be located.

    Real exam PDFs print a citation's parts on SEPARATE LINES — an AP FRQ renders
    ``3. Sterling silver...`` then ``(b) The following table...`` then ``(ii) Using
    principles of atomic structure...``. So '3(b)(ii)' never appears as one token, and a
    naive line-prefix probe reports every sub-part citation absent while also matching
    'Q1(a)' against '1 atmosphere equals 760...' on the formula sheet. Measured on the
    live corpus, that probe was wrong in BOTH directions on 3 of 5 real citations.

    This instead walks the structure: find the question-number opener, take the span up to
    the next opener, and require each sub-part token to appear as its own line-initial
    ``(x)`` inside it.

    ADVISORY, NOT PROOF. A ``None`` means "could not confirm from this PDF's text", never
    "fabricated" — layout, OCR quirks and per-board conventions all produce false
    negatives. The audit reports it for human triage; only ``quote_supported`` (content
    grounding, against the quote the model actually copied) is strong enough to gate on.
    """
    if paper is None:
        return None
    tokens = question_ref_tokens(ref)
    if not tokens:
        return None
    number, subs = tokens[0], tokens[1:]
    for start, (_, line) in enumerate(paper.lines):
        if _opener_number(line) != number:
            continue
        # The span runs to the next DIFFERENT question opener (a non-opener yields None,
        # which must not terminate it).
        end = next((j for j in range(start + 1, len(paper.lines))
                    if _opener_number(paper.lines[j][1]) not in (number, None)),
                   len(paper.lines))
        span = paper.lines[start:end]
        if all(any(_sub_part(t).match(ln) for _, ln in span) for t in subs):
            return paper.lines[start][0]
    return None


def extract_text(pdf_bytes: bytes) -> "PaperText | None":
    """Extracted text of a PDF, or ``None`` when nothing can be proven from it.

    Returns None for: PyMuPDF not importable, unparseable bytes, or NO TEXT LAYER (a
    scan). All three FAIL CLOSED — a caller holding None must publish zero citations.

    A missing PyMuPDF is a broken install, not a property of the document, so it SHOUTS
    (once — the ``helpers._LF_WARNED`` idiom) rather than degrading in silence: a run that
    quietly stopped verifying citations is the same blind spot as an untraced call.
    """
    global _FITZ_WARNED
    try:
        import fitz  # PyMuPDF — soft import, mirrors ground_specs.slice_pdf
    except Exception:  # noqa: BLE001
        if not _FITZ_WARNED:
            _FITZ_WARNED = True
            print("    PYMUPDF MISSING — paper citations CANNOT be verified and are ALL being "
                  "dropped. Install it (`py -3 -m pip install pymupdf`) to restore them.")
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        raw, lines = [], []
        for i in range(doc.page_count):
            page = doc.load_page(i).get_text("text")
            raw.append(page)
            lines.extend((i + 1, ln) for ln in page.splitlines() if ln.strip())
        flat = normalise("\n".join(raw))
        pages = doc.page_count
    except Exception as exc:  # noqa: BLE001
        print(f"    pdf text extraction failed: {exc}")
        return None
    if len(flat) < MIN_DOC_CHARS:
        print(f"    pdf has no usable text layer ({len(flat)} chars - likely a scan); "
              "citations disallowed for this paper")
        return None
    return PaperText(flat, lines, pages)
