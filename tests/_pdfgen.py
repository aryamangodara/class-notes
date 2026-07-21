"""Build a REAL PDF in pure stdlib — shared test fixture, not a test itself.

Why this exists. The past-paper stub used to be ``b"%PDF-1.4 stub bytes"``: enough to
clear ``fetch_pdf``'s ``%PDF-`` magic check, but not a parseable document. That was fine
while "verified" meant "a second model agreed" — the test never needed the PDF to contain
anything. Now that a citation must be PROVEN against the PDF's own text, a stub that
cannot be parsed can only ever exercise the DROP path, and the positive path would be
untestable.

The alternatives were all worse: a committed binary fixture (``tests/`` has no data files
and a binary blob is unreviewable), a new dependency (none is acceptable for an offline
smoke), or building it with ``fitz`` (which would make the test's positive path depend on
the very library under test). So: ~20 lines of byte-exact PDF, no dependencies at all.

Verified: the output opens in PyMuPDF with ``doc.is_repaired == False`` — the xref table
is genuinely correct, not silently rebuilt by the parser's repair fallback.
"""
from __future__ import annotations


def make_pdf(lines: "list[str]") -> bytes:
    """A one-page PDF with a real text layer containing ``lines``, byte-exact.

    Pass ``[]`` for a valid PDF with NO extractable text — the offline stand-in for a
    scanned paper, which must yield zero citations.
    """
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    body = ("BT /F1 12 Tf 72 720 Td 14 TL\n"
            + "".join(f"({esc(ln)}) Tj T*\n" for ln in lines)
            + "ET").encode("latin-1", "replace")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>stream\n" % len(body) + body + b"\nendstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + obj + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return bytes(out)


# A plausible exam page in the two conventions the registry's boards actually print, taken
# from the real PDFs: AP FRQ opens a question with "3. " (number + period) and puts each
# sub-part on its OWN line, while Edexcel opens with "6\t" (number + layout tab). A
# citation therefore NEVER appears as a single "3(b)(ii)" token anywhere in the text, which
# is the whole reason `locate_question` walks the structure instead of matching a prefix.
#
# Deliberate traps, each one a real failure observed on the live corpus:
#   * "2." exists but has no "(c)", so "2(c)(i)" must NOT locate;
#   * a bare "12" page-number line and a "0.800 mol dm-3" data line must not read as
#     question openers (an earlier looser pattern matched them and cut spans short);
#   * prose long enough that a quote drawn from it clears MIN_QUOTE_CHARS/MIN_RUN_CHARS.
PAPER_LINES = [
    "1. A student investigates the combustion of methane in a bomb calorimeter.",
    "(a) State Hess's law and explain why it follows from conservation of energy. (2)",
    "(b) Calculate the enthalpy change of combustion of methane using the data below. (4)",
    "12",
    "2. The table shows 12 circles arranged in a square lattice of side length 4 cm.",
    "3. Sterling silver is an alloy of silver and copper used to make jewellery.",
    "(a) What are the oxidation numbers of silver and copper in the alloy? (1)",
    "(b) The following table contains the atomic radii for silver and copper.",
    "(i) Explain why sterling silver is better classified as a substitutional alloy. (2)",
    "(ii) Explain why the second ionisation energy of magnesium is larger than the first.",
    "The first ionisation energy of magnesium is 738 kJ mol-1 as printed in the data booklet.",
    "0.800 mol dm-3 of sodium hydroxide solution was added from a burette.",
    "6\t Benzoic acid is a weak acid found in cranberries and other soft fruit.",
    "(d)\t Weak acids such as benzoic acid can be neutralised by sodium hydroxide solution.",
]
