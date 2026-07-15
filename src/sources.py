"""Curated per-board source registry for past-paper generation (genai-free).

Where do the REAL exam PDFs live, per board+subject? This is the curated,
hand-authored map the past-paper stage consults before fetching — the citations
themselves are generated + verified against the fetched PDF (``past_papers.py``),
but the URLs and signposting here are human-curated (the same licence discipline as
the image policy).

Lawful availability is patchy by board: AP publishes FRQs + scoring guidelines
(official, free; MCQs are never released); Edexcel papers are downloadable (official
Pearson, or a PMT rehost); Cambridge IGCSE papers are copyright and mostly gated; the
digital SAT publishes only practice tests. Where no lawful paper PDF exists, ``papers``
is empty and the stage degrades to resources-only signposting.

Seed URLs are official where possible (AP FRQ PDFs and the board qualification pages
verified against the live sites; Edexcel chem papers reuse the human-verified links
already in the enthalpy topic). Add a (board, subject) entry to ``papers_by_subject``
to switch on verified citations for more subjects.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from schemas import ExamMapCell

_AP_HOSTS = ("apcentral.collegeboard.org",)
_EDEXCEL_HOSTS = ("qualifications.pearson.com", "pmt.physicsandmathstutor.com", "physicsandmathstutor.com")
_CAMBRIDGE_HOSTS = ("cambridgeinternational.org",)
_SAT_HOSTS = ("satsuite.collegeboard.org", "collegeboard.org")


@dataclass(frozen=True)
class PaperSource:
    label: str                       # authoritative paper identity, e.g. "2024 · Free-response questions"
    url: str                         # direct PDF url, fetched at runtime
    kind: str = "official"           # "official" | "rehost" | "practice"
    has_mcq: bool = False            # AP FRQ PDFs contain no MCQs
    license_note: str = ""


@dataclass(frozen=True)
class SpecSource:
    url: str
    citation: str
    page_hint_keywords: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class BoardSources:
    intro: str
    disclaimer: str
    how_to: str
    fetch_allowlist: "tuple[str, ...]"
    subject_hub: "dict[str, str]" = field(default_factory=dict)          # subject -> official page url
    rehost: "ExamMapCell | None" = None                                  # optional third-party signpost
    papers_by_subject: "dict[str, list[PaperSource]]" = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedSources:
    intro: str
    disclaimer: str
    where_to_get: "list[ExamMapCell]"          # -> PastPapers.resources (every board)
    papers: "list[PaperSource]"                # fetchable PDFs for this topic's subject (may be empty)
    fetch_allowlist: "tuple[str, ...]"
    spec_source: "SpecSource | None"


_REGISTRY: "dict[str, BoardSources]" = {
    "AP (College Board)": BoardSources(
        intro="Once you can clear the ladder above, sit real College Board free-response "
              "questions under timed conditions. FRQs and their scoring guidelines are published "
              "free every year — mark your own work against the official rubric.",
        disclaimer="Question summaries are written in our own words and verified against the "
                   "official FRQ PDFs — attempt the originals. AP multiple-choice questions are "
                   "secure and not released, so only free-response is cited here.",
        how_to="Do the FRQ untimed first, then to time; mark against the scoring guidelines and "
               "log every lost point against the checklist above.",
        fetch_allowlist=_AP_HOSTS,
        subject_hub={
            "Chemistry": "https://apcentral.collegeboard.org/courses/ap-chemistry/exam/past-exam-questions",
            "Biology":   "https://apcentral.collegeboard.org/courses/ap-biology/exam/past-exam-questions",
            "Physics":   "https://apcentral.collegeboard.org/courses/ap-physics-1/exam/past-exam-questions",
        },
        papers_by_subject={
            "Chemistry": [
                PaperSource("2024 · Free-response questions",
                            "https://apcentral.collegeboard.org/media/pdf/ap24-frq-chemistry.pdf",
                            license_note="Official College Board FRQ (MCQs not released)."),
                PaperSource("2023 · Free-response questions",
                            "https://apcentral.collegeboard.org/media/pdf/ap23-frq-chemistry.pdf"),
            ],
            "Biology": [
                PaperSource("2024 · Free-response questions",
                            "https://apcentral.collegeboard.org/media/pdf/ap24-frq-biology.pdf"),
                PaperSource("2023 · Free-response questions",
                            "https://apcentral.collegeboard.org/media/pdf/ap23-frq-biology.pdf"),
            ],
            "Physics": [
                PaperSource("2024 · Free-response questions",
                            "https://apcentral.collegeboard.org/media/pdf/ap24-frq-physics-1.pdf"),
                PaperSource("2023 · Free-response questions",
                            "https://apcentral.collegeboard.org/media/pdf/ap23-frq-physics-1.pdf"),
            ],
        },
    ),
    "Edexcel A-Level": BoardSources(
        intro="Once you can clear the ladder above, sit actual Edexcel questions under timed "
              "conditions — nothing calibrates you like the real paper. Start with the most "
              "recent series.",
        disclaimer="Question summaries are written in our own words and verified against the "
                   "papers themselves — attempt the originals on the official papers.",
        how_to="Timed, no notes, then mark ruthlessly with the scheme. Log every lost mark "
               "against the checklist above — that becomes your revision list.",
        fetch_allowlist=_EDEXCEL_HOSTS,
        subject_hub={
            "Chemistry":   "https://qualifications.pearson.com/en/qualifications/edexcel-a-levels/chemistry-2015.html",
            "Mathematics": "https://qualifications.pearson.com/en/qualifications/edexcel-a-levels/mathematics-2017.html",
            "Physics":     "https://qualifications.pearson.com/en/qualifications/edexcel-a-levels/physics-2015.html",
        },
        rehost=ExamMapCell(
            key="Topic-sorted questions",
            value="[Physics & Maths Tutor](https://www.physicsandmathstutor.com/) collects past "
                  "questions filtered by topic (a third-party rehost of the official papers)"),
        papers_by_subject={
            "Chemistry": [
                PaperSource(
                    "June 2024 · Paper 1",
                    "https://pmt.physicsandmathstutor.com/download/Chemistry/A-level/Past-Papers/"
                    "Edexcel/Paper-1/June%202024%20QP%20-%20Paper%201%20Edexcel%20Chemistry%20A-level.pdf",
                    kind="rehost", license_note="Third-party rehost (PMT) of the official Edexcel paper."),
                PaperSource(
                    "June 2023 · Paper 1",
                    "https://pmt.physicsandmathstutor.com/download/Chemistry/A-level/Past-Papers/"
                    "Edexcel/Paper-1/June%202023%20QP%20-%20Paper%201%20Edexcel%20Chemistry%20A-level.pdf",
                    kind="rehost"),
            ],
        },
    ),
    "Cambridge IGCSE": BoardSources(
        intro="Practise with real Cambridge past papers under timed conditions once the ladder "
              "above feels comfortable.",
        disclaimer="Cambridge past papers are copyright Cambridge International; access them "
                   "through your school or the official site. Citations are not auto-generated "
                   "here until a lawful paper source is configured.",
        how_to="Work a full paper to time, then mark against the official mark scheme.",
        fetch_allowlist=_CAMBRIDGE_HOSTS,
        subject_hub={
            "Chemistry": "https://www.cambridgeinternational.org/programmes-and-qualifications/"
                         "cambridge-igcse-chemistry-0620/past-papers/",
            "Biology":   "https://www.cambridgeinternational.org/programmes-and-qualifications/"
                         "cambridge-igcse-biology-0610/past-papers/",
            "Physics":   "https://www.cambridgeinternational.org/programmes-and-qualifications/"
                         "cambridge-igcse-physics-0625/past-papers/",
        },
        # papers_by_subject empty — Cambridge PDFs are gated; degrade to resources-only.
    ),
    "SAT (College Board)": BoardSources(
        intro="The digital SAT doesn't release past forms, but College Board publishes full "
              "official practice tests — sit them in Bluebook under real timing.",
        disclaimer="Only official practice tests are available for the digital SAT (no past exam "
                   "forms are released), so there are no past-paper citations here.",
        how_to="Take a full Bluebook practice test to time; review every miss, especially the "
               "wrong options built around sign slips and swapped slope/intercept.",
        fetch_allowlist=_SAT_HOSTS,
        subject_hub={
            "Mathematics": "https://satsuite.collegeboard.org/practice/practice-tests",
            "Reading and Writing": "https://satsuite.collegeboard.org/practice/practice-tests",
        },
        # papers_by_subject empty — practice tests live in Bluebook, not as fetchable PDFs.
    ),
    "AMC (MAA)": BoardSources(
        intro="Once these techniques are automatic, train on real AMC papers under the 75-minute clock. "
              "Every past AMC is published with full official solutions — the best practice there is.",
        disclaimer="Practise with official past AMC papers; the problems and full solutions are freely "
                   "available from the MAA and the Art of Problem Solving wiki. No citations are "
                   "auto-generated here.",
        how_to="Sit a full 25-question set to time with no calculator, then study the official solution "
               "to every problem you missed OR guessed — the review is where competition scores grow.",
        fetch_allowlist=("maa.org", "artofproblemsolving.com"),
        subject_hub={
            "Mathematics": "https://artofproblemsolving.com/wiki/index.php/AMC_10_Problems_and_Solutions",
        },
        # papers_by_subject empty — link to the problem archive; no single-topic PDFs to fetch.
    ),
}


# Official spec / CED PDFs, keyed (board, subject) — used by extract_specs.py (fetch) and
# ground_specs.py (verify). Seeded from official sites; a (board, subject) with no entry is
# skipped (reported, never guessed). A URL is registered ONLY after confirming it resolves
# to a real PDF (never invented — the grounding moat).

# Every AP (College Board) subject with an official CED PDF, each URL verified to resolve at
# the standard apcentral pattern. "Physics" = AP Physics 1 (matches the existing sample);
# Calculus AB & BC share one combined CED.
_AP_CED = "https://apcentral.collegeboard.org/media/pdf/ap-{}-course-and-exam-description.pdf"
_AP_SUBJECTS: "dict[str, str]" = {
    "Biology": "biology",
    "Chemistry": "chemistry",
    "Physics": "physics-1",
    "Physics 2": "physics-2",
    "Physics C: Mechanics": "physics-c-mechanics",
    "Physics C: Electricity and Magnetism": "physics-c-electricity-and-magnetism",
    "Calculus AB and BC": "calculus-ab-and-bc",
    "Precalculus": "precalculus",
    "Statistics": "statistics",
    "Computer Science A": "computer-science-a",
    "Computer Science Principles": "computer-science-principles",
    "Microeconomics": "microeconomics",
    "Macroeconomics": "macroeconomics",
    "Psychology": "psychology",
    "Human Geography": "human-geography",
    "Environmental Science": "environmental-science",
    "United States History": "us-history",
    "World History: Modern": "world-history-modern",
    "European History": "european-history",
    "United States Government and Politics": "us-government-and-politics",
    "Comparative Government and Politics": "comparative-government-and-politics",
    "English Language and Composition": "english-language-and-composition",
    "English Literature and Composition": "english-literature-and-composition",
}

_SPEC_SOURCES: "dict[tuple[str, str], SpecSource]" = {
    **{("AP (College Board)", _subj): SpecSource(
        _AP_CED.format(_slug), f"AP {_subj} Course and Exam Description (College Board)")
       for _subj, _slug in _AP_SUBJECTS.items()},
    ("Edexcel A-Level", "Chemistry"): SpecSource(
        "https://qualifications.pearson.com/content/dam/pdf/A%20Level/Chemistry/2015/"
        "Specification%20and%20sample%20assessments/a-level-chemistry-2015-specification.pdf",
        "Pearson Edexcel A-Level Chemistry (9CH0) specification"),
    ("Edexcel A-Level", "Physics"): SpecSource(
        "https://qualifications.pearson.com/content/dam/pdf/A%20Level/Physics/2015/"
        "Specification%20and%20sample%20assessments/a-level-physics-2015-specification.pdf",
        "Pearson Edexcel A-Level Physics (9PH0) specification"),
    ("Cambridge IGCSE", "Chemistry"): SpecSource(
        "https://www.cambridgeinternational.org/Images/595428-2023-2025-syllabus.pdf",
        "Cambridge IGCSE Chemistry (0620) syllabus 2023-2025"),
}


def resolve_sources(spec) -> "ResolvedSources | None":
    """Resolve curated sources for a topic's board+subject, or None if the board is
    unknown. ``where_to_get`` is always populated (signposting for every board);
    ``papers`` is empty when no lawful paper PDF is configured (→ resources-only)."""
    board = _REGISTRY.get(spec.board)
    if board is None:
        return None
    subject = spec.subject
    resources: "list[ExamMapCell]" = []
    hub = board.subject_hub.get(subject)
    if hub:
        resources.append(ExamMapCell(
            key="Official papers + mark schemes",
            value=f"Download from the [official {spec.board} {subject} page]({hub})"))
    if board.rehost is not None:
        resources.append(board.rehost)
    resources.append(ExamMapCell(key="How to use them", value=board.how_to))
    return ResolvedSources(
        intro=board.intro,
        disclaimer=board.disclaimer,
        where_to_get=resources,
        papers=list(board.papers_by_subject.get(subject, [])),
        fetch_allowlist=board.fetch_allowlist,
        spec_source=_SPEC_SOURCES.get((spec.board, subject)),
    )
