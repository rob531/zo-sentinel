"""
gate.py -- the discrimination acceptance gate (pure, hermetic).

Design of record: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md §2.4.

The RED poles reuse the in-repo degeneracy oracle
(anomaly_detector.detect_score_clustering_anomaly: stddev < 0.01 == "Scoring
is not discriminating."; >= 90% in one 0.1-bucket == clustering). Acceptance
is deliberately far stricter than the pathology thresholds: a student adapter
is promoted only when its scores SPREAD, ORDER risk, and ABSTAIN correctly.

Pure function over score lists -- no bus, no network -- so the lane and the
hermetic tests run the identical gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# Acceptance thresholds (design §2.4). The oracle's RED poles are far below:
# stddev 0.01 / cluster 0.90 are the measured pathology; these are the floor a
# *useful* scorer must clear.
MIN_STDDEV = 10.0            # 0-100 scale
MAX_CLUSTER_RATIO = 0.50     # max share of scores in one 0.1-wide bucket
MIN_SEPARATION = 25.0        # mean(trusted) - mean(threat), points
MIN_AUC = 0.80               # pairwise P(trusted > threat)
MIN_NULL_ABSTAIN = 0.90      # INSUFFICIENT rate on the evidence-null slice
MAX_TOTAL_ABSTAIN = 0.35     # INSUFFICIENT rate over the whole eval slice
MIN_SAMPLE = 10              # below this nothing can be claimed -> RED


@dataclass
class GateResult:
    green: bool
    checks: Dict[str, bool] = field(default_factory=dict)
    numbers: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.green


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stddev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _cluster_ratio(xs: Sequence[float]) -> float:
    """Share of scores in the most-populated 0.1-wide bucket (the
    anomaly_detector bucketing, round to 1 decimal)."""
    if not xs:
        return 1.0
    buckets: Dict[float, int] = {}
    for x in xs:
        b = round(x, 1)
        buckets[b] = buckets.get(b, 0) + 1
    return max(buckets.values()) / len(xs)


def _auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    """Pairwise P(pos > neg), ties count half. O(n*m) -- eval slices are small."""
    if not pos or not neg:
        return 0.0
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def evaluate_scores(
    scores: Sequence[float],
    *,
    trusted_scores: Optional[Sequence[float]] = None,
    threat_scores: Optional[Sequence[float]] = None,
    null_slice_abstained: Optional[int] = None,
    null_slice_total: Optional[int] = None,
    total_abstained: Optional[int] = None,
) -> GateResult:
    """Evaluate the student's eval-slice scores against the acceptance gate.

    `scores` alone exercises the degeneracy poles (spread + clustering).
    The separation / abstention checks run only when their inputs are given --
    a check with no input is recorded as a named reason, never silently
    skipped-as-passed (GC-5: no silent mutation of the question).
    """
    res = GateResult(green=True)
    xs = [float(s) for s in scores]

    if len(xs) < MIN_SAMPLE:
        res.green = False
        res.checks["sample"] = False
        res.numbers["n"] = float(len(xs))
        res.reasons.append(f"RED: n={len(xs)} < {MIN_SAMPLE}; nothing can be claimed")
        return res
    res.checks["sample"] = True
    res.numbers["n"] = float(len(xs))

    sd = _stddev(xs)
    res.numbers["stddev"] = sd
    res.checks["spread"] = sd >= MIN_STDDEV
    if not res.checks["spread"]:
        res.reasons.append(
            f"RED: stddev={sd:.3f} < {MIN_STDDEV} -- scoring is not discriminating")

    cr = _cluster_ratio(xs)
    res.numbers["cluster_ratio"] = cr
    res.checks["anti_clustering"] = cr < MAX_CLUSTER_RATIO
    if not res.checks["anti_clustering"]:
        res.reasons.append(
            f"RED: {cr * 100:.1f}% of scores share one 0.1-bucket "
            f"(limit {MAX_CLUSTER_RATIO * 100:.0f}%)")

    if trusted_scores is not None and threat_scores is not None:
        sep = _mean(trusted_scores) - _mean(threat_scores)
        auc = _auc(trusted_scores, threat_scores)
        res.numbers["separation"] = sep
        res.numbers["auc"] = auc
        res.checks["separation"] = sep >= MIN_SEPARATION and auc >= MIN_AUC
        if not res.checks["separation"]:
            res.reasons.append(
                f"RED: separation={sep:.1f} (need >={MIN_SEPARATION}) "
                f"auc={auc:.3f} (need >={MIN_AUC})")
    else:
        res.reasons.append("separation check NOT RUN (no labelled slices supplied)")

    if null_slice_total:
        rate = (null_slice_abstained or 0) / null_slice_total
        res.numbers["null_abstain_rate"] = rate
        res.checks["null_abstention"] = rate >= MIN_NULL_ABSTAIN
        if not res.checks["null_abstention"]:
            res.reasons.append(
                f"RED: abstains on only {rate * 100:.0f}% of evidence-null rows "
                f"(need >={MIN_NULL_ABSTAIN * 100:.0f}%)")
    else:
        res.reasons.append("null-abstention check NOT RUN (no evidence-null slice)")

    if total_abstained is not None:
        rate = total_abstained / len(xs)
        res.numbers["total_abstain_rate"] = rate
        res.checks["abstention_ceiling"] = rate <= MAX_TOTAL_ABSTAIN
        if not res.checks["abstention_ceiling"]:
            res.reasons.append(
                f"RED: abstains on {rate * 100:.0f}% of the whole eval slice "
                f"(ceiling {MAX_TOTAL_ABSTAIN * 100:.0f}%)")

    res.green = all(res.checks.values())
    return res
