"""Offline self-test for the PDF-grounded past-paper stage (no key/network).

Stubs ``helpers.call_model`` + ``helpers._http_get`` so the two-pass logic, the
safety invariants (url from the registry, summary sanitised + rendered via
textContent) and graceful degradation are exercised without a Gemini key or a real
fetch. Mirrors the dependency-light discipline of ``_smoke_v2.py``.
"""
import inspect
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))   # app modules live in src/
sys.path.insert(0, os.path.join(_ROOT, "tests"))  # shared fixture builder

import helpers  # noqa: E402
import past_papers as pp  # noqa: E402
import pdf_text  # noqa: E402
import render_v2  # noqa: E402
from _pdfgen import PAPER_LINES, make_pdf  # noqa: E402
from schemas import (  # noqa: E402
    CandidateCitations, CitationVerdict, ExamMapCell, PaperCitationCandidate,
    PastPapers, VerificationReport, VerifiedPaper,
)
from schemas_v2 import Hero, InteractiveNotes  # noqa: E402
from sources import PaperSource, resolve_sources  # noqa: E402

# 1. pure url/summary guards.
AL = ("apcentral.collegeboard.org",)
assert pp.safe_http_url("https://apcentral.collegeboard.org/media/pdf/ap24-frq-chemistry.pdf", AL)
assert pp.safe_http_url("http://sub.apcentral.collegeboard.org/x.pdf", AL)
assert pp.safe_http_url("javascript:alert(1)", AL) == ""
assert pp.safe_http_url("https://evil.example.com/x.pdf", AL) == ""
assert pp.sanitize_summary("a <b>bold</b>\n  spaced") == "a bold spaced"
print("pure guards OK (allowlist scheme+host, tag strip)")

# 2. confirmed_to_verified: drop unconfirmed; identity (url + label) from the registry.
#    Quotes are drawn from the fixture PDF, because `confirmed` alone is no longer enough
#    to ship a citation — see 2b.
_REAL_Q3 = "Explain why the second ionisation energy of magnesium is larger than the first"
_REAL_Q1 = "Calculate the enthalpy change of combustion of methane using the data below"
_PAPER_TEXT = pdf_text.extract_text(make_pdf(PAPER_LINES))
paper = PaperSource("2024 · FRQ", "https://apcentral.collegeboard.org/media/pdf/ap24-frq-chemistry.pdf")
cands = [
    PaperCitationCandidate(paper_label="2024 · FRQ", question="Q3", marks=10, summary="PES",
                           evidence_quote=_REAL_Q3, topic_relevance="PES"),
    PaperCitationCandidate(paper_label="2024 · FRQ", question="Q7", marks=4, summary="off-topic",
                           evidence_quote=_REAL_Q1, topic_relevance="weak"),
]
verds = [
    CitationVerdict(paper_label="2024 · FRQ", question="Q3", confirmed=True, marks_ok=True,
                    verified_summary="Uses PES to deduce electron configuration."),
    CitationVerdict(paper_label="2024 · FRQ", question="Q7", confirmed=False, reason="not this topic"),
]
vps = pp.confirmed_to_verified(cands, verds, paper, AL, paper_text=_PAPER_TEXT)
assert len(vps) == 1 and "Q3" in vps[0].label and "Q7" not in vps[0].label, "only confirmed kept"
assert vps[0].url == paper.url, "url is the registry url, never model text"
assert vps[0].summary == "Uses PES to deduce electron configuration."
assert pp.confirmed_to_verified(cands, verds, PaperSource("x", "https://evil.com/x.pdf"), AL,
                                paper_text=_PAPER_TEXT) == [], "off-allowlist url -> none"
print("confirmed_to_verified OK (1/2 kept; url+label from registry)")

# 2b. THE REGRESSION: a confirmed verdict whose evidence is NOT in the fetched PDF is
#     dropped. `confirmed` is a boolean a second model produced, and model-checking-model
#     fails in the same direction — this is the gate that stops an invented question with
#     an invented data table from shipping as "verified". The drop is PER CITATION: its
#     siblings survive, so one bad quote never collapses a good panel.
_fake = PaperCitationCandidate(
    paper_label="2024 · FRQ", question="Q9", marks=6, summary="invented",
    evidence_quote="The four elements listed have first ionisation energies of 738, 1451 and 7733",
    topic_relevance="looks plausible")
_fake_v = CitationVerdict(paper_label="2024 · FRQ", question="Q9", confirmed=True, marks_ok=True,
                          verified_summary="Deduces identity from successive ionisation energies.")
_mixed = pp.confirmed_to_verified(cands + [_fake], verds + [_fake_v], paper, AL,
                                  paper_text=_PAPER_TEXT)
assert all("Q9" not in v.label for v in _mixed), "a confirmed verdict with fabricated evidence is dropped"
assert len(_mixed) == 1 and "Q3" in _mixed[0].label, "the fabricated citation drops; its siblings survive"
assert pp.confirmed_to_verified(cands, verds, paper, AL, paper_text=None) == [], \
    "no extractable PDF text (a scan) yields NO citations - cannot prove, do not claim"
print("evidence gate OK (fabricated quote dropped individually; no text -> none)")

# 2c. The `null` label bug: these placeholders are all TRUTHY, so the old `if q else ''`
#     guard rendered `June 2023 · Paper 1 · null · 2 marks` to students.
for _bad in ("null", "NONE", "N/A", " n/a. ", "Unknown", "-", "--", "?", "", "   "):
    assert pp.clean_question_ref(_bad) == "", f"{_bad!r} is not a question reference"
assert pp.clean_question_ref("Q2(c)(i)") == "Q2(c)(i)", "a real reference survives"
assert pp.clean_question_ref(" Q6\n(d) ") == "Q6 (d)", "whitespace is collapsed, not rejected"
#     The original bug was `q = (v.corrected_question or v.question or "")`: a TRUTHY
#     "null" in corrected_question short-circuits the `or` and MASKS a perfectly good
#     question ref. So the fix must RECOVER here, not drop — `corrected_question` is only
#     set "if the candidate's was wrong", and a model writing null-as-a-string into an
#     optional field almost always means "no correction needed".
_nullv = [CitationVerdict(paper_label="2024 · FRQ", question="Q3", corrected_question="null",
                          confirmed=True, marks_ok=True, verified_summary="s")]
_rec = pp.confirmed_to_verified(cands, _nullv, paper, AL, paper_text=_PAPER_TEXT)
assert len(_rec) == 1 and "· Q3 ·" in _rec[0].label, \
    "a nullish correction falls through to the real question ref rather than masking it"
assert "null" not in _rec[0].label, "the placeholder never reaches a rendered label"
#     Only when EVERY reference is a placeholder is there no citation to make.
_allnull = [CitationVerdict(paper_label="2024 · FRQ", question="null", corrected_question="n/a",
                            confirmed=True, marks_ok=True, verified_summary="s")]
assert pp.confirmed_to_verified([], _allnull, paper, AL, paper_text=_PAPER_TEXT) == [], \
    "a citation the model cannot name at all is not a citation"
print("clean_question_ref OK (10 placeholders rejected; nullish correction recovers the real ref)")

# 2d. STRUCTURAL enforcement, mirroring the `trace` contract in _smoke_tracing.py: a
#     keyword-only parameter with NO default cannot be forgotten silently by a new call
#     site — it fails at the call instead of quietly reopening the hole.
_p = inspect.signature(pp.confirmed_to_verified).parameters
assert _p["paper_text"].default is inspect.Parameter.empty, \
    "`paper_text` must have NO default - that is the enforcement"
assert _p["paper_text"].kind is inspect.Parameter.KEYWORD_ONLY, \
    "`paper_text` must be keyword-only so a new call site cannot pass it positionally by luck"
assert not hasattr(pp, "degrade"), \
    "dead `degrade()` removed - the real fallback is build_past_papers' empty-list tail"
print("evidence-gate signature OK (keyword-only, no default; degrade() gone)")

# 3. build_past_papers end-to-end with stubbed model + fetch.
_CAND, _VER = CandidateCitations(items=cands), VerificationReport(items=verds)


def _stub_call_model(client, *, trace, **kwargs):
    # `trace` is REQUIRED (no default), so a stage that ever went out untraced would fail
    # here with a TypeError rather than quietly stubbing. Keyed off the declared stage
    # vocabulary — see tests/_smoke_tracing.py for the contract itself.
    if trace["stage"] == "past_papers.candidates":
        return _CAND
    if trace["stage"] == "past_papers.verify":
        return _VER
    raise AssertionError("unexpected call_model stage: " + trace["stage"])


def _stub_http_ok(url, timeout=30, *, max_bytes=None, accept_types=None):
    # A REAL PDF with a real text layer, not `b"%PDF-1.4 stub bytes"`. Once a citation
    # must be PROVEN against the document, an unparseable stub can only ever exercise the
    # drop path — so `assert panel.verified` below now means what it always claimed to.
    return make_pdf(PAPER_LINES)


def _stub_http_scan(url, timeout=30, *, max_bytes=None, accept_types=None):
    return make_pdf([])          # a valid PDF with NO text layer, i.e. a scanned paper


def _stub_http_fail(url, timeout=30, *, max_bytes=None, accept_types=None):
    raise OSError("network down")


_real_cm, _real_hg = helpers.call_model, helpers._http_get
spec = helpers.load_topic_spec("ap-chem-atomic-structure-periodicity")  # AP Chemistry -> 2 FRQ papers
try:
    helpers.call_model, helpers._http_get = _stub_call_model, _stub_http_ok
    panel = pp.build_past_papers(None, spec)
    assert panel is not None and panel.verified, "AP chem should yield verified citations"
    assert all(v.url.startswith("https://apcentral.collegeboard.org") for v in panel.verified), "urls from registry"
    assert all("Q7" not in v.label for v in panel.verified), "unconfirmed candidate dropped"
    assert panel.resources, "resources[] signposting present"
    n_ok = len(panel.verified)

    helpers._http_get = _stub_http_scan  # scanned paper -> unprovable -> resources-only
    scanned = pp.build_past_papers(None, spec)
    assert scanned is not None and scanned.verified == [] and scanned.resources, \
        "a scanned paper yields NO citations but keeps its signposting"

    helpers._http_get = _stub_http_fail  # fetch failure -> resources-only degrade
    degraded = pp.build_past_papers(None, spec)
    assert degraded is not None and degraded.verified == [] and degraded.resources, "fetch-fail degrades cleanly"
finally:
    helpers.call_model, helpers._http_get = _real_cm, _real_hg
print(f"build_past_papers OK ({n_ok} verified with model; [] on scan and on fetch-fail)")

# 4. resolve_sources: AP has papers; IGCSE degrades; unknown board -> None.
assert resolve_sources(spec).papers, "AP chem has fetchable papers"
_r = resolve_sources(helpers.load_topic_spec("igcse-chem-electrolysis"))
assert _r is not None and _r.papers == [] and _r.where_to_get, "IGCSE resources-only"


class _Unknown:
    board, subject, topic = "Nonexistent Board", "Chemistry", "T"


assert resolve_sources(_Unknown()) is None, "unknown board -> None"
print("resolve_sources OK (AP papers; IGCSE resources-only; unknown None)")

# 5. render neutralisation: a model summary/url cannot inject.
evil = PastPapers(intro="i", disclaimer="d", resources=[ExamMapCell(key="k", value="v")],
                  verified=[VerifiedPaper(label="X", summary="pwn</script><img src=x onerror=alert(1)>",
                                          url="javascript:alert(1)")])
notes = InteractiveNotes(topic_id="t", board="AP (College Board)", subject="Chemistry", level="AP",
                         unit="U", topic="T", hero=Hero(eyebrow="e", title="t", lede="l"), past_papers=evil)
html = render_v2.render_interactive_html(notes)
assert "pwn</script>" not in html, "raw </script> from a field must be escaped in the data island"
assert 'href="javascript:' not in html, "no javascript: href in output"
js = render_v2._JS
assert "safeUrl(vp.url)" in js, "verified url must pass through the safeUrl guard"
assert "md(vp.summary" not in js, "verified summary must NOT go through md()/innerHTML"
print("render neutralisation OK (escaped island; safeUrl guard; summary via textContent)")

print("\nALL PAST-PAPER SMOKE CHECKS PASSED")
