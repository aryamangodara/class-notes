"""Offline test for the deterministic spot-check sampler (no key/network)."""
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import spotcheck  # noqa: E402

ids = [f"topic-{i:03d}" for i in range(100)]
a = spotcheck.select_sample(ids, 20)
assert a == spotcheck.select_sample(ids, 20), "sample must be deterministic across runs"
assert len(a) == 5, f"~1/20 of 100 = 5, got {len(a)}"
assert set(a) <= set(ids), "sample is a subset of the ids"
assert spotcheck.select_sample(list(reversed(ids)), 20) == a, "sample independent of input order"
assert len(spotcheck.select_sample(ids, 10)) == 10, "rate scales the sample"
assert spotcheck.select_sample(["only-one"], 20) == ["only-one"], ">=1 when non-empty"
assert spotcheck.select_sample([], 20) == [], "empty in -> empty out"
assert "Spot-check" in spotcheck.review_template("x") and "needs-fix" in spotcheck.review_template("x")
print("spotcheck OK (deterministic; ~1/rate; order-independent; >=1; template)")

print("\nALL SPOTCHECK SMOKE CHECKS PASSED")
