#!/usr/bin/env python3
"""calibration.py -- the DEFAULT-OFF severity remap for the axis classifier.

WHAT WAS MEASURED (adjudicated calibration, 2026-09-03)
-------------------------------------------------------
A stratified blind sample of the FULL DB corpus (model_version v3.0_40974559,
population 296,109) was rated independently, and every row where the two raters
disagreed was adjudicated. Fitting a single integer ladder shift on half the
adjudicated rows and scoring it on the other half (CALIBRATION.json) says the
classifier is systematically TOO SEVERE, and that a shift of -1 helps on
exactly two axes:

    overall_risk        33.3% -> 55.6%   (+22.2pp, held-out n=36)
    capability_breadth  29.4% -> 52.9%   (+23.5pp, held-out n=17)

and helps not at all on the other three ordinal axes:

    data_sensitivity     20.69% -> 20.69%  (0.0pp, held-out n=29)
    exploit_surface      51.28% -> 51.28%  (0.0pp, held-out n=39)
    network_egress       59.09% -> 59.09%  (0.0pp, held-out n=22)

On those three the error is NOT a constant offset, and moving them would only
trade one set of mistakes for another. Hence SHIFTS carries two axes and no
others.

WHY THIS IS DEFAULT OFF
-----------------------
1. THE FIT SAW ONLY CONTESTED ROWS. Adjudication ran on the 121 items where the
   two raters DISAGREED. The ~57% of axis rows where they AGREED were never
   adjudicated and never entered the fit or the held-out half. So the +22pp and
   +23.5pp above are improvements measured on the disagreement subset ONLY. It
   is NOT known whether the same -1 shift DEGRADES the agreed rows -- on a row
   both raters already got right, a -1 shift can only ever make it wrong. Until
   a full-sample evaluation exists (see eval_calibration.py, which scores the
   shift across ALL sampled rows including the agreed ones), the sign of the
   net effect on the corpus is genuinely unknown.

   MEASURED SINCE (2026-09-03, eval_calibration.py, same artifacts): the sign
   is negative. Across all 581 reconstructible sampled rows the -1 shift scores
   overall_risk 64.06% -> 51.56% (-12.50pp, n=128) and capability_breadth
   83.61% -> 45.90% (-37.70pp, n=122). The contested-only gain does NOT survive
   contact with the rows the two raters agreed on. Caveat, stated plainly: zo
   is one of the two raters, so on an agreed row truth == zo's own label and
   the no-shift baseline is 100% there by construction -- run
   eval_calibration.py and read its agreed/contested columns separately rather
   than quoting the blended delta alone. Either way nothing here argues for
   turning the flag on, and the default stays OFF.

2. TURNING IT ON MOVES THE PUBLIC NUMBER. overall_risk is the input to
   apply_risk_tier_backfill.py, which stamps mcp_server_registry.risk_tier. A
   -1 shift on overall_risk therefore moves the PUBLIC risk_tier on the order
   of 296,109 servers -- one flag flip, the whole visible corpus reclassified
   one notch less severe.

So: DEFAULT OFF, behind ZO_CALIBRATION_V2, pending a full-sample evaluation.
With the flag off this module is a strict no-op and the emitted
decision_rule_version stays gate_rule_v1_2026-06-16.

Pure functions, stdlib only, no DB and no network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

try:                                          # same-directory sibling module
    from score_validity import AXIS_LABELS
except ImportError:                           # pragma: no cover - import shim
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from score_validity import AXIS_LABELS

# The canonical ladders are NOT retyped here. score_validity.AXIS_LABELS is
# copied verbatim from the cross-repo contract
# (rob531/zomesh-sentinel-sft :: schemas/risk_axis_mapping_v1.json) and its
# header forbids editing it from observed output. Derive, never duplicate.
LADDERS: Dict[str, Tuple[str, ...]] = dict(AXIS_LABELS)

# The measured remap. Two axes, both -1. Adding an axis here without a
# held-out measurement to back it is exactly the defect this file documents.
SHIFTS: Dict[str, int] = {"overall_risk": -1, "capability_breadth": -1}

# An explicit "I cannot determine this" must never be shifted into a positive
# claim. capability_breadth's ladder ENDS in UNKNOWN (index 3), so a naive -1
# would silently rewrite "don't know" as "BROAD" -- a fabricated finding, and
# the one thing this layer must never do. maintainer_trust's off-ladder marker
# is included for the same reason even though that axis is not in SHIFTS.
NEVER_SHIFT_LABELS = frozenset({"UNKNOWN", "UNKNOWN_AUTHOR"})

RULE_V1 = "gate_rule_v1_2026-06-16"
RULE_V2 = "gate_rule_v2_2026-09-03"

ENV_FLAG = "ZO_CALIBRATION_V2"
_TRUE = frozenset({"1", "true", "yes"})


def calibration_enabled() -> bool:
    """True only when ZO_CALIBRATION_V2 is exactly 1/true/yes (any case).

    Anything else -- unset, empty, "0", "on", "TRUE-ish typos" -- is False.
    The default is OFF and an ambiguous value must not read as consent.
    """
    return os.environ.get(ENV_FLAG, "").strip().lower() in _TRUE


def rule_version(enabled: bool) -> str:
    """The decision_rule_version to stamp on emitted rows.

    A row may only claim v2 when the remap actually ran. Stamping v2 while the
    remap is off would make the ledger unfalsifiable after the fact.
    """
    return RULE_V2 if enabled else RULE_V1


def escalation_gate(orp: Sequence[float]) -> Tuple[bool, Optional[str], float]:
    """The production escalation rule, extracted VERBATIM from weekly_rescore.

    This is a lift-and-shift of the nested ``gate(orp)`` closure in
    ph_import so that it can be tested without a DB. Same 0.40 / 0.30 cutoffs,
    same (escalated, escalated_to, p_critical) return shape, same defensive
    len() checks, same behaviour on a short or empty probability vector.
    Behaviour must NOT change here -- the -1 remap deliberately does not touch
    escalation, which still reads the RAW overall_risk probabilities.
    """
    pcrit = orp[3] if len(orp) > 3 else 0.0
    phigh = orp[2] if len(orp) > 2 else 0.0
    if pcrit >= 0.40:
        return True, "CRITICAL", pcrit
    if pcrit + phigh >= 0.30:
        return True, "REVIEW", pcrit
    return False, None, pcrit


def _label_at(ladder: Sequence[str], i: int) -> str:
    try:
        return ladder[i]
    except (IndexError, TypeError, KeyError):
        return ""


def _prob_at(probs: Any, i: int, fallback: float) -> float:
    """probs[i] as a float, or ``fallback`` if the vector cannot supply it.

    A short/absent probs list is real: weekly_rescore reads
    ``pr.get(axis, [])`` and some records carry no vector at all. Guarding here
    means a degenerate row keeps the p_top it already had rather than raising
    or writing a null.
    """
    try:
        v = probs[i]
    except (IndexError, TypeError, KeyError):
        return fallback
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return fallback
    return float(v)


class _Unset:
    """Sentinel: distinguishes "not supplied" from a supplied None."""

    def __repr__(self) -> str:                # pragma: no cover - debug aid
        return "<unset>"


_UNSET = _Unset()


def apply_calibration(
    axis: str,
    label_index: int,
    probs: Any,
    ladder: Sequence[str],
    enabled: bool,
    p_top: Any = _UNSET,
    label: Any = _UNSET,
) -> Tuple[int, str, float]:
    """Return ``(new_index, new_label, new_p_top)`` for one axis row.

    Rules, in order:
      * ``enabled`` False           -> inputs returned unchanged. THE DEFAULT.
      * ``axis`` not in SHIFTS      -> unchanged.
      * current label is an explicit "don't know" (NEVER_SHIFT_LABELS)
                                    -> unchanged. Never promote an UNKNOWN.
      * otherwise index += SHIFTS[axis], clamped into [0, len(ladder)-1].

    ``new_p_top`` is ALWAYS the probability of the label actually emitted --
    ``probs[new_index]`` -- so the row stays internally coherent and a reader
    can never see a p_top that belongs to a label that was not written.

    ``p_top`` and ``label`` are OPTIONAL passthroughs (not in the original
    spec). When no shift is applied they are returned byte-identical, which is
    what makes the disabled path a strict no-op even for a degenerate row whose
    probs vector is short or whose label string does not round-trip through the
    ladder. Omit them and the no-shift return is derived from the ladder and
    the probs vector instead, which for a well-formed row is the same value.
    """
    cur_label = label if label is not _UNSET else _label_at(ladder, label_index)
    cur_p = _prob_at(probs, label_index, 0.0 if p_top is _UNSET else p_top)

    def unchanged() -> Tuple[int, str, float]:
        return label_index, cur_label, (cur_p if p_top is _UNSET else p_top)

    if not enabled:
        return unchanged()
    shift = SHIFTS.get(axis)
    if not shift:
        return unchanged()
    if cur_label in NEVER_SHIFT_LABELS:
        return unchanged()
    if not ladder:
        return unchanged()
    if not isinstance(label_index, int) or isinstance(label_index, bool):
        return unchanged()
    if label_index < 0 or label_index >= len(ladder):
        return unchanged()

    new_index = max(0, min(len(ladder) - 1, label_index + shift))
    if new_index == label_index:
        return unchanged()
    new_label = ladder[new_index]
    if new_label in NEVER_SHIFT_LABELS:      # never shift INTO a don't-know
        return unchanged()
    return new_index, new_label, _prob_at(probs, new_index, cur_p)
