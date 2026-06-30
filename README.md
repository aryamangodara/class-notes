# Class Notes Generator (POC)

Feed a **topic** for an exam board (AP / IGCSE / SAT / A-Level) and generate
**curriculum-grounded class notes** for it — rendered to Markdown, a
self-contained HTML page (LaTeX + diagrams), and JSON.

Sister project to `../Grader`: same Gemini stack, same `.env`, same
`config / schemas / helpers / prompts` split. Where the Grader reads *how a
topic is assessed* to score answers, this reads the *same curriculum* to teach
toward it.

## Why this isn't just "ask an AI to write notes"

A generic prompt blends every board together and drifts. The value here is
**grounding**: each topic carries the *exact* syllabus learning objectives,
a **depth profile** (how far this board takes the idea), and **assessment
notes** (how it's examined). Generation is then checked against that contract.

```
topic ──► [1] outline ──► [2] draft each section ──► [3] finalize ──► [4] verify coverage ──► render
          (covers every     (grounded, parallel)     (key terms,        (every objective    (md/html/json)
           objective)                                 misconceptions,     actually taught?)
                                                      exam tips,
                                                      practice)
```

Step 4 is what makes it more than a prompt: a per-objective coverage audit, plus
review flags for low-confidence facts — surfaced, not hidden (Grader philosophy).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then add GEMINI_API_KEY  (the Grader's key works)
```

## Run

```bash
python notes.py --list                                    # seeded topics
python notes.py ap-bio-cellular-respiration               # one topic
python notes.py --all                                     # all four
```

Outputs land in `out/<topic_id>.{md,html,json}`. Open the `.html` in a browser
for rendered maths and diagrams.

## Seeded topics (one per board, to prove depth calibration)

| topic_id | board | what it demonstrates |
|---|---|---|
| `ap-bio-cellular-respiration` | AP Biology | conceptual depth; CED *exclusion* of intermediates |
| `igcse-bio-photosynthesis` | Cambridge IGCSE | Core vs Supplement tiering; required practicals |
| `sat-math-linear-equations` | SAT Math | skills (not content) map; interpret-in-context |
| `alevel-maths-differentiation-first-principles` | Edexcel A-Level | proof rigour; the `lim` step-change |

Compare the SAT vs A-Level maths notes side by side — same subject, deliberately
different rigour. That contrast is the whole point of grounding.

## Add a topic or board

Drop a new `curriculum/<topic_id>.json` matching the `TopicSpec` schema
(`schemas.py`). It's discovered automatically — no code change. For production,
generate these maps from official spec PDFs using `prompts/spec_extract.txt`
(reuses the Grader's PDF-extraction technique).

## Layout

```
config.py      models, temperatures, house style
schemas.py     Pydantic: TopicSpec (grounding) + ClassNotes (output) — also Gemini response_schema
helpers.py     gemini client (mirrored), pipeline stages, renderers
prompts/       outline / write_section / finalize / verify / spec_extract  (plain text, edit freely)
curriculum/    one TopicSpec JSON per topic — the grounding store
notes.py       CLI: feed a topic
out/           generated notes (gitignored)
```

## Roadmap (beyond the POC)

- **Curriculum maps from PDFs** — wire `spec_extract.txt` to parse official CED /
  syllabus PDFs into `curriculum/*.json` (validate the hand-seeded specs).
- **Custom diagrams** — POC does Mermaid + LaTeX; commission/generate the hard
  bespoke illustrations (labelled biology, free-body diagrams).
- **PDF / DOCX export** — printable, teacher-editable notes.
- **Teacher-in-the-loop** — a review step on flagged content before publish.
- **S3 distribution** — reuse the Grader's `upload_to_s3.py`.
- **Claude option** — provider is swappable; A/B against Gemini on the same specs.
