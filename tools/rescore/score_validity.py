#!/usr/bin/env python3
"""score_validity.py -- what a VALID classifier output looks like, as code.

Chairman 2026-07-25: "shouldn't really need a canary. Just needs an
understanding of what a valid output looks like."

For three weeks every scoring run reported success while emitting noise: the
adapter never reached the pod (.gitignore ate the weights, FU-093), so eval ran
on base Qwen2.5-3B with RANDOM HEADS. Nothing caught it because every gate
checked a PROXY for success -- row count, preds count, `degraded=false`, an
adapter sha hashed off the LOCAL disk -- and never the SEMANTIC property: does
this output look like a classifier's output at all?

It does not take a paid canary to answer that. A 4-class classifier scoring
86,000 heterogeneous servers CANNOT emit one label 100% of the time. Random
heads collapse to a single arbitrary class (a DIFFERENT one per run, since the
init seed differs) -- exactly the fingerprint the moat carries:

    2026-07-18   86,050 servers   100.0% HIGH        <- random heads
    2026-07-21   65,045 servers   100.0% CRITICAL    <- random heads
    2026-07-24  125,731 servers   100.0% LOW         <- random heads
    2026-06-24 .. 07-03  ~1,200   MEDIUM/HIGH/LOW/CRITICAL mix  <- real

This module makes that judgement mechanical and runs it at IMPORT, so invalid
output is structurally unable to enter the moat. Pure functions, stdlib only,
no DB and no network -- the caller supplies rows.

Axis label space is the cross-repo contract (schemas/risk_axis_mapping_v1.json
in rob531/zomesh-sentinel-sft). NOTE auth_strength has exactly FOUR classes --
never infer an enum from sample output, which is how random-head noise gets
mistaken for a schema.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple

# ---- the contract ------------------------------------------------------
AXIS_LABELS: Dict[str, Tuple[str, ...]] = {
    "overall_risk":       ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
    "auth_strength":      ("STRONG", "MODERATE", "WEAK", "UNKNOWN"),   # 4, not 6
    "capability_breadth": ("NARROW", "MODERATE", "BROAD", "UNKNOWN"),
    "data_sensitivity":   ("LOW", "MEDIUM", "HIGH", "UNKNOWN"),
    "network_egress":     ("NONE", "LIMITED", "BROAD", "UNKNOWN"),
    "maintainer_trust":   ("TRUSTED", "COMMUNITY", "UNKNOWN", "UNTRUSTED"),
    "exploit_surface":    ("LOW", "MEDIUM", "HIGH", "UNKNOWN"),
}

# ---- thresholds (loop-set, per the autopoiesis doctrine) ---------------
MAX_LABEL_SHARE = 0.95   # no single label may own >95% of a large cohort
MIN_DISTINCT_LABELS = 2  # a real classifier discriminates
MIN_ENTROPY_BITS = 0.15  # ~0 bits == collapsed to one class
MIN_COHORT = 500         # below this a uniform result can be legitimate

VALID, DEGENERATE, SCHEMA_VIOLATION, INSUFFICIENT = (
    "VALID", "DEGENERATE", "SCHEMA_VIOLATION", "INSUFFICIENT")


def entropy_bits(counts: Iterable[int]) -> float:
    """Shannon entropy of a label histogram. Collapsed distribution -> ~0."""
    c = [n for n in counts if n > 0]
    total = sum(c)
    if total <= 0:
        return 0.0
    return -sum((n / total) * math.log2(n / total) for n in c)


def validate_axis(axis: str, labels: Sequence[str],
                  min_cohort: int = MIN_COHORT) -> dict:
    """Verdict for ONE axis over a cohort of predicted labels.

    SCHEMA_VIOLATION beats every other verdict: a label outside the contract
    means we are not even looking at this classifier's output.
    """
    n = len(labels)
    hist = Counter(labels)
    allowed = AXIS_LABELS.get(axis)
    unknown_labels = sorted(set(hist) - set(allowed)) if allowed else []
    if allowed and unknown_labels:
        return {"axis": axis, "n": n, "verdict": SCHEMA_VIOLATION,
                "offending_labels": unknown_labels, "histogram": dict(hist)}
    if n == 0:
        return {"axis": axis, "n": 0, "verdict": INSUFFICIENT,
                "reason": "no rows", "histogram": {}}
    top_label, top_n = hist.most_common(1)[0]
    share = top_n / n
    bits = entropy_bits(hist.values())
    base = {"axis": axis, "n": n, "top_label": top_label,
            "top_share": round(share, 4), "distinct_labels": len(hist),
            "entropy_bits": round(bits, 4), "histogram": dict(hist)}
    if n < min_cohort:
        # Too small to judge: say so. Never let a small cohort PASS as proof,
        # and never condemn it either -- absence of evidence is a finding.
        return {**base, "verdict": INSUFFICIENT,
                "reason": "cohort {} < {}".format(n, min_cohort)}
    if (share > MAX_LABEL_SHARE or len(hist) < MIN_DISTINCT_LABELS
            or bits < MIN_ENTROPY_BITS):
        return {**base, "verdict": DEGENERATE,
                "reason": ("{} owns {:.1%} of {} rows, {} distinct, {:.3f} bits"
                           " -- the random-head fingerprint").format(
                               top_label, share, n, len(hist), bits)}
    return {**base, "verdict": VALID}


def validate_run(rows: Iterable[dict], min_cohort: int = MIN_COHORT) -> dict:
    """Gate a whole scoring run. rows: {"axis_name":..., "label":...}.

    A run is IMPORTABLE only if no axis is DEGENERATE or SCHEMA_VIOLATION.
    INSUFFICIENT never authorises an import on its own.
    """
    by_axis: Dict[str, List[str]] = {}
    for r in rows:
        by_axis.setdefault(r["axis_name"], []).append(r["label"])
    axes = [validate_axis(a, lb, min_cohort) for a, lb in sorted(by_axis.items())]
    bad = [a for a in axes if a["verdict"] in (DEGENERATE, SCHEMA_VIOLATION)]
    judged = [a for a in axes if a["verdict"] == VALID]
    return {"axes": axes,
            "n_axes": len(axes),
            "n_valid": len(judged),
            "n_bad": len(bad),
            "importable": (not bad) and bool(judged),
            "verdict": (SCHEMA_VIOLATION
                        if any(a["verdict"] == SCHEMA_VIOLATION for a in bad)
                        else DEGENERATE if bad
                        else VALID if judged else INSUFFICIENT)}


def assert_importable(rows: Iterable[dict], min_cohort: int = MIN_COHORT) -> dict:
    """Fail-CLOSED import gate. Raises SystemExit on anything not VALID.

    Wire this in front of the import phase: garbage then cannot reach the moat,
    no matter what the row counts or `degraded` flag say.
    """
    report = validate_run(rows, min_cohort)
    if not report["importable"]:
        detail = "; ".join(
            "{}: {} ({})".format(a["axis"], a["verdict"],
                                 a.get("reason") or a.get("offending_labels"))
            for a in report["axes"] if a["verdict"] != VALID)
        raise SystemExit(
            "ABORT import: scores are not valid classifier output [{}] -- {}. "
            "Refusing to write garbage to the moat (FU-093/FU-094).".format(
                report["verdict"], detail))
    return report


if __name__ == "__main__":
    # Fixtures are the REAL observed prod distributions -- this suite is a
    # regression test against the actual 3-week incident.
    def rows(axis, hist):
        return [{"axis_name": axis, "label": l}
                for l, n in hist.items() for _ in range(n)]

    def labels(axis, hist):
        return [r["label"] for r in rows(axis, hist)]

    # --- the three garbage waves MUST be rejected ---
    g0724 = validate_axis("overall_risk", labels("overall_risk",
                                                 {"LOW": 125726, "CRITICAL": 5}))
    assert g0724["verdict"] == DEGENERATE, g0724
    g0721 = validate_axis("overall_risk", ["CRITICAL"] * 65045)
    assert g0721["verdict"] == DEGENERATE and g0721["distinct_labels"] == 1, g0721
    g0718 = validate_axis("overall_risk", labels("overall_risk",
                                                 {"HIGH": 86049, "CRITICAL": 1}))
    assert g0718["verdict"] == DEGENERATE, g0718
    ga = validate_axis("auth_strength", labels("auth_strength",
                                               {"WEAK": 125723, "UNKNOWN": 8}))
    assert ga["verdict"] == DEGENERATE, ga

    # --- real, mixed output MUST pass (6/24-7/03 shape, scaled to judgeable size) ---
    real = validate_axis("overall_risk", labels(
        "overall_risk", {"MEDIUM": 910, "HIGH": 300, "LOW": 140, "CRITICAL": 70}))
    assert real["verdict"] == VALID, real
    assert real["entropy_bits"] > MIN_ENTROPY_BITS

    # --- schema violation beats everything (a label off-contract) ---
    sv = validate_axis("auth_strength", ["STRONG"] * 600 + ["TOTALLY_BOGUS"] * 600)
    assert sv["verdict"] == SCHEMA_VIOLATION, sv
    assert sv["offending_labels"] == ["TOTALLY_BOGUS"], sv
    # auth_strength is 4 classes -- a 6-class assumption is how noise passes as schema
    assert len(AXIS_LABELS["auth_strength"]) == 4

    # --- small cohorts are INSUFFICIENT: never a free pass, never condemned ---
    small = validate_axis("overall_risk", ["LOW"] * 10)
    assert small["verdict"] == INSUFFICIENT, small

    # --- run-level gate ---
    bad_run = validate_run(rows("overall_risk", {"LOW": 125726, "CRITICAL": 5}))
    assert bad_run["importable"] is False and bad_run["verdict"] == DEGENERATE
    good_run = validate_run(rows(
        "overall_risk", {"MEDIUM": 910, "HIGH": 300, "LOW": 140, "CRITICAL": 70}))
    assert good_run["importable"] is True and good_run["verdict"] == VALID

    # --- the gate must actually RAISE (an uncalled helper is not a gate) ---
    raised = False
    try:
        assert_importable(rows("overall_risk", {"LOW": 125726, "CRITICAL": 5}))
    except SystemExit as e:
        raised = "ABORT import" in str(e)
    assert raised, "assert_importable failed to fail-closed"
    assert_importable(rows(
        "overall_risk", {"MEDIUM": 910, "HIGH": 300, "LOW": 140, "CRITICAL": 70}))

    print("PASS score_validity self-tests "
          "(all 3 garbage waves REJECTED, real distribution ACCEPTED)")
