"""Offline self-test for the shipped-citation audit (no key/network).

The audit re-checks citations that were "verified" by a second model only, on pages that
are already in front of students. Two properties matter more than any single verdict: it
must never call a model, and it must never write to a generated note. Both are asserted
STATICALLY below rather than trusted, because a future edit could quietly add either.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))   # app modules live in src/
sys.path.insert(0, os.path.join(_ROOT, "tests"))  # shared fixture builder

import audit_citations as ac  # noqa: E402
import pdf_text  # noqa: E402
from _pdfgen import PAPER_LINES, make_pdf  # noqa: E402

# 1. parse_label round-trips what confirmed_to_verified builds. It is only ever used to
#    RE-CHECK a claim, never to make one, so an unparseable label degrades rather than
#    raising — the paper identity itself contains middots, so the question ref is the LAST
#    segment, not the second.
assert ac.parse_label("June 2024 · Paper 1 · Q2(c)(i) · 5 marks") == ("June 2024 · Paper 1", "Q2(c)(i)", 5)
assert ac.parse_label("2024 · Free-response questions · 3(b)(ii)") == ("2024 · Free-response questions", "3(b)(ii)", None)
assert ac.parse_label("June 2024 · Paper 1 · Q5 · 10 marks")[1] == "Q5", "marks suffix is stripped, not read as the ref"
assert ac.parse_label("2023 · FRQ · Q1(a) · 4 points")[2] == 4, "AP points parse like marks"
assert ac.parse_label("")[1] == "" and ac.parse_label("Solo")[1] == "", "an unparseable label yields no ref"
#    The trap that position-based parsing falls into: REGISTRY paper labels contain
#    middots themselves, so a paper-only label must not have its own name read as a
#    question and then audited as missing.
assert ac.parse_label("2024 · Free-response questions") == ("2024 · Free-response questions", "", None), \
    "a paper-only label yields no question ref rather than parsing its own name as one"
assert ac.parse_label("June 2024 · Paper 1")[1] == "", "a two-segment paper name is not a question"
print("parse_label OK (ref must LOOK like one; marks/points stripped; degrades quietly)")

# 2. audit_citation verdicts. `unconfirmed` is deliberately NOT a fabrication verdict —
#    on the live corpus a naive probe called 3 of 5 GENUINE citations absent, so a
#    location miss is triage for a human, never proof.
_PAPER = pdf_text.extract_text(make_pdf(PAPER_LINES))
assert ac.audit_citation("2024 · FRQ · 3(b)(ii)", _PAPER)[0] == "located", "a real sub-part citation locates"
assert ac.audit_citation("June 2023 · Paper 1 · Q6(d) · 5 marks", _PAPER)[0] == "located", \
    "the Edexcel tab convention locates too"
assert ac.audit_citation("2024 · FRQ · 2(c)(i)", _PAPER)[0] == "unconfirmed", \
    "a sub-part that is not in the paper cannot be confirmed"
assert ac.audit_citation("2024 · FRQ", _PAPER)[0] == "no-ref", "a label with no question ref is actionable"
assert "weak" in ac.audit_citation("June 2024 · Paper 1 · Q2 · 10 marks", _PAPER)[1], \
    "a bare question number is reported as weak evidence, not a clean bill"
assert "weak" not in ac.audit_citation("2024 · FRQ · 3(b)(ii)", _PAPER)[1], \
    "a sub-part citation constrains enough not to be weak"
print("audit_citation OK (located / unconfirmed / no-ref; bare numbers flagged weak)")

# 3. --strict must NOT fail on `unconfirmed`. The probe is format-sensitive; failing a
#    build on it would train everyone to ignore the audit.
assert "unconfirmed" not in ac.FAIL_VERDICTS, "a location miss is advisory, never a build failure"
assert "no-ref" in ac.FAIL_VERDICTS and "unfetchable" in ac.FAIL_VERDICTS, \
    "a dead link and a ref-less label are hard facts about our own artifact"
print("strict-verdict policy OK (hard facts fail; advisory misses do not)")

# 4. STATIC guards — the two properties the audit's whole value rests on. Asserted from
#    the AST rather than by behaviour, so they hold for code paths this test never runs.
with open(os.path.join(_ROOT, "src", "audit_citations.py"), encoding="utf-8") as fh:
    _tree = ast.parse(fh.read(), filename="audit_citations.py")


def _called(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")


_calls = [_called(n) for n in ast.walk(_tree) if isinstance(n, ast.Call)]
assert "call_model" not in _calls and "generate_content" not in _calls, \
    "the audit must make ZERO model calls - it re-checks shipped claims, it does not make new ones"

_fns = [n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)]


def _owner(node):
    """The function a call sits in, by line-range containment (as _smoke_tracing does)."""
    return next((f.name for f in _fns if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)),
                "<module>")


# Every write must live in `main`, where the destination is built from CONFIG["out_dir"].
# The functions that handle an AUDITED note (audit_note / audit_citation / parse_label)
# must not write at all — that is what keeps a read-only audit read-only.
_writes = [(_owner(n), n.lineno) for n in ast.walk(_tree)
           if isinstance(n, ast.Call) and _called(n) in ("write_text", "write_bytes", "open")]
assert _writes and all(o == "main" for o, _ in _writes), \
    f"every write must sit in main(), never in a note-handling function: {_writes}"
assert "dump" not in _calls, \
    "no json.dump straight onto a file handle - reports go through the report dir only"
print(f"static guards OK (no model call; all {len(_writes)} write(s) confined to main)")

print("\nALL CITATION-AUDIT SMOKE CHECKS PASSED")
