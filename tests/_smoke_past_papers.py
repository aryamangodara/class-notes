"""Offline self-test for the PDF-grounded past-paper stage (no key/network).

Stubs ``helpers.call_model`` + ``helpers._http_get`` so the two-pass logic, the
safety invariants (url from the registry, summary sanitised + rendered via
textContent) and graceful degradation are exercised without a Gemini key or a real
fetch. Mirrors the dependency-light discipline of ``_smoke_v2.py``.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)               # anchor CWD-relative paths to the repo root
sys.path.insert(0, os.path.join(_ROOT, "src"))  # app modules live in src/

import helpers  # noqa: E402
import past_papers as pp  # noqa: E402
import render_v2  # noqa: E402
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
paper = PaperSource("2024 · FRQ", "https://apcentral.collegeboard.org/media/pdf/ap24-frq-chemistry.pdf")
cands = [
    PaperCitationCandidate(paper_label="2024 · FRQ", question="Q3", marks=10, summary="PES",
                           evidence_quote="photoelectron", topic_relevance="PES"),
    PaperCitationCandidate(paper_label="2024 · FRQ", question="Q7", marks=4, summary="off-topic",
                           evidence_quote="x", topic_relevance="weak"),
]
verds = [
    CitationVerdict(paper_label="2024 · FRQ", question="Q3", confirmed=True, marks_ok=True,
                    verified_summary="Uses PES to deduce electron configuration."),
    CitationVerdict(paper_label="2024 · FRQ", question="Q7", confirmed=False, reason="not this topic"),
]
vps = pp.confirmed_to_verified(cands, verds, paper, AL)
assert len(vps) == 1 and "Q3" in vps[0].label and "Q7" not in vps[0].label, "only confirmed kept"
assert vps[0].url == paper.url, "url is the registry url, never model text"
assert vps[0].summary == "Uses PES to deduce electron configuration."
assert pp.confirmed_to_verified(cands, verds, PaperSource("x", "https://evil.com/x.pdf"), AL) == [], "off-allowlist url -> none"
print("confirmed_to_verified OK (1/2 kept; url+label from registry)")

# 3. build_past_papers end-to-end with stubbed model + fetch.
_CAND, _VER = CandidateCitations(items=cands), VerificationReport(items=verds)


def _stub_call_model(client, *, label="", **kwargs):
    if label.startswith("pp-cand"):
        return _CAND
    if label.startswith("pp-verify"):
        return _VER
    raise AssertionError("unexpected call_model label: " + label)


def _stub_http_ok(url, timeout=30, *, max_bytes=None, accept_types=None):
    return b"%PDF-1.4 stub bytes"


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

    helpers._http_get = _stub_http_fail  # fetch failure -> resources-only degrade
    degraded = pp.build_past_papers(None, spec)
    assert degraded is not None and degraded.verified == [] and degraded.resources, "fetch-fail degrades cleanly"
finally:
    helpers.call_model, helpers._http_get = _real_cm, _real_hg
print(f"build_past_papers OK ({n_ok} verified with model; [] on fetch-fail)")

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
