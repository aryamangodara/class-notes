#!/usr/bin/env python3
"""Deterministic 1-in-20 subject-tutor spot-check sampler.

Automated passes (the coverage gate, structural checks, PDF verifiers) can't catch
every conceptual slip at volume. This deterministically samples ~1 page in 20 from
out/ and bundles each with a reviewer checklist into out/spotcheck/, so a subject
tutor reviews a stable, reproducible slice — the SAME pages every run (re-running
never reshuffles the sample), and it scales as the corpus grows.

    py -3 src/spotcheck.py            # sample ~1/20 of out/**/*.v2.json
    py -3 src/spotcheck.py --rate 10  # sample ~1/10 instead
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from config import CONFIG


def select_sample(ids: "list[str]", rate: int = 20) -> "list[str]":
    """Deterministically pick ~len(ids)/rate ids (>=1 when non-empty). Ranked by the
    sha1 of the id (NOT the randomised built-in hash), so the sample is stable across
    runs and independent of input order, and it scales with the corpus."""
    ranked = sorted(ids, key=lambda i: hashlib.sha1(i.encode("utf-8")).hexdigest())
    if not ranked:
        return []
    k = (len(ranked) + rate - 1) // rate  # ceil -> always >= 1 when non-empty
    return sorted(ranked[:k])


def review_template(topic_id: str, flags: "list[str] | None" = None) -> str:
    body = (
        f"# Spot-check — {topic_id}\n\n"
        f"Open `{topic_id}.interactive.html` and check the CONCEPTUAL accuracy the "
        "automated passes can't:\n\n"
        "- [ ] Every worked example / derivation is mathematically + scientifically correct\n"
        "- [ ] Definitions use the board's required wording (marks hinge on it)\n"
        "- [ ] No over-reach beyond the depth profile; no wrong oversimplification\n"
        "- [ ] Numeric answers, units and significant figures are right\n"
        "- [ ] MCQ 'correct' options are truly correct; distractor feedback is sound\n"
        "- [ ] Spec codes shown match the official spec\n"
        "- [ ] Past-paper 'Verified' citations point to real questions (spot 1-2)\n\n"
        "Reviewer: __________   Date: __________   Verdict: pass / needs-fix\n\n"
        "Notes:\n"
    )
    # Advisory model-verifier flags for THIS page (the tiered gate routes them here
    # rather than blocking on them: a single model read can be wrong, so a human
    # adjudicates each — confirm a real defect, or dismiss a false positive).
    if flags:
        listed = "\n".join(f"- [ ] {f}" for f in flags)
        body += (
            "\n---\n\n"
            "## Model review flags — ADVISORY, adjudicate each\n\n"
            "The generator's own verifier raised these. They are deliberately NOT gated "
            "(deterministic defects already are; a model *opinion* can be a false "
            "positive), so decide per flag: confirm a real defect → needs-fix, or "
            "dismiss it.\n\n"
            f"{listed}\n"
        )
    return body


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Deterministic 1-in-N spot-check sampler.")
    ap.add_argument("--rate", type=int, default=20, help="sample ~1 in RATE pages (default 20)")
    args = ap.parse_args()

    out = Path(CONFIG["out_dir"])
    dest = out / "spotcheck"
    # Notes are grouped on disk as out/<board>/<subject>/<id>.v2.json — discover them
    # recursively (skipping our own bundle dir) and remember each id's path so the
    # sibling .interactive.html can be found next to it.
    by_id: "dict[str, Path]" = {}
    for p in sorted(out.rglob("*.v2.json")):
        if "spotcheck" in p.relative_to(out).parts:
            continue
        by_id[p.name[: -len(".v2.json")]] = p
    ids = sorted(by_id)
    if not ids:
        print(f"No generated notes in {out}/ (looking for **/*.v2.json). Generate some first.")
        return
    sample = select_sample(ids, args.rate)
    dest.mkdir(parents=True, exist_ok=True)
    for tid in sample:
        jpath = by_id[tid]
        html = jpath.with_name(f"{tid}.interactive.html")
        if html.exists():
            shutil.copyfile(html, dest / html.name)
        else:
            print(f"  (no interactive.html for {tid}; writing checklist only)")
        flags: "list[str]" = []
        try:
            flags = json.loads(jpath.read_text(encoding="utf-8")).get("review_flags") or []
        except Exception:  # noqa: BLE001 — a malformed JSON must not abort the bundle
            flags = []
        (dest / f"{tid}.review.md").write_text(review_template(tid, flags), encoding="utf-8")
    print(f"Sampled {len(sample)}/{len(ids)} page(s) (~1 in {args.rate}, deterministic) -> {dest}/")
    for tid in sample:
        print(f"  - {tid}")
    print("Give each .review.md to a subject tutor alongside its .interactive.html.")


if __name__ == "__main__":
    main()
