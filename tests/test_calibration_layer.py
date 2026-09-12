"""Unit tests for tools/rescore/calibration.py -- the DEFAULT-OFF severity remap.

DB-free, network-free. The two properties worth most here are negative:
  * with the flag off the layer is a STRICT no-op, and
  * an explicit "don't know" is never promoted into a positive claim.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RESCORE = Path(__file__).resolve().parents[1] / "tools" / "rescore"
if str(RESCORE) not in sys.path:
    sys.path.insert(0, str(RESCORE))

from calibration import (  # noqa: E402
    LADDERS, NEVER_SHIFT_LABELS, RULE_V1, RULE_V2, SHIFTS, apply_calibration,
    calibration_enabled, escalation_gate, rule_version)

OVERALL = LADDERS["overall_risk"]            # LOW MEDIUM HIGH CRITICAL
BREADTH = LADDERS["capability_breadth"]      # NARROW MODERATE BROAD UNKNOWN


# ---------------------------------------------------------------- the ladders

def test_ladders_come_from_the_canonical_contract():
    """Not retyped here or in calibration.py -- derived from score_validity."""
    from score_validity import AXIS_LABELS
    assert LADDERS == dict(AXIS_LABELS)
    assert OVERALL == ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert BREADTH[-1] == "UNKNOWN"          # the live UNKNOWN case


def test_shifts_names_exactly_the_two_measured_axes():
    assert SHIFTS == {"overall_risk": -1, "capability_breadth": -1}


# ------------------------------------------------- escalation_gate regression

@pytest.mark.parametrize("orp,expected", [
    # pcrit >= 0.40 -> CRITICAL, whatever phigh is
    ([0.0, 0.0, 0.0, 0.40], (True, "CRITICAL", 0.40)),
    ([0.0, 0.0, 0.0, 0.99], (True, "CRITICAL", 0.99)),
    ([0.1, 0.1, 0.5, 0.41], (True, "CRITICAL", 0.41)),
    # pcrit + phigh >= 0.30 -> REVIEW
    ([0.0, 0.0, 0.30, 0.00], (True, "REVIEW", 0.00)),
    ([0.0, 0.0, 0.10, 0.20], (True, "REVIEW", 0.20)),
    ([0.0, 0.0, 0.01, 0.39], (True, "REVIEW", 0.39)),
    # below both cutoffs -> no escalation
    ([0.5, 0.3, 0.10, 0.10], (False, None, 0.10)),
    ([1.0, 0.0, 0.00, 0.00], (False, None, 0.00)),
    ([0.0, 0.0, 0.29, 0.00], (False, None, 0.00)),
])
def test_escalation_gate_cutoffs(orp, expected):
    assert escalation_gate(orp) == expected


@pytest.mark.parametrize("orp,expected", [
    ([], (False, None, 0.0)),                 # empty
    ([0.9], (False, None, 0.0)),              # 1 long
    ([0.5, 0.5], (False, None, 0.0)),         # 2 long: no phigh, no pcrit
    ([0.0, 0.0, 0.9], (True, "REVIEW", 0.0)), # 3 long: phigh only
    ([0.0, 0.0, 0.1], (False, None, 0.0)),
])
def test_escalation_gate_short_vectors(orp, expected):
    """Defensive len() checks preserved verbatim from the nested closure."""
    assert escalation_gate(orp) == expected


def test_escalation_gate_boundaries_are_inclusive():
    assert escalation_gate([0, 0, 0, 0.3999])[1] == "REVIEW"
    assert escalation_gate([0, 0, 0, 0.40])[1] == "CRITICAL"
    assert escalation_gate([0, 0, 0.10, 0.19])[0] is False
    assert escalation_gate([0, 0, 0.20, 0.10])[1] == "REVIEW"


# ------------------------------------------------------- disabled = strict no-op

@pytest.mark.parametrize("axis", sorted(LADDERS))
def test_disabled_is_a_strict_no_op_on_every_axis(axis):
    ladder = LADDERS[axis]
    probs = [0.05] * len(ladder)
    for i, lbl in enumerate(ladder):
        probs_i = list(probs)
        probs_i[i] = 0.7
        out = apply_calibration(axis, i, probs_i, ladder, False)
        assert out == (i, lbl, 0.7), "{}[{}] moved with the flag OFF".format(axis, i)


def test_disabled_returns_index_label_and_p_top_identical():
    probs = [0.1, 0.2, 0.6, 0.1]
    idx, label, p_top = apply_calibration("overall_risk", 2, probs, OVERALL, False)
    assert idx == 2
    assert label == "HIGH"
    assert p_top == 0.6
    assert p_top == probs[idx]


def test_disabled_passthrough_keeps_the_callers_own_label_and_p_top():
    """weekly_rescore passes its own label/p_top; off means byte-identical."""
    out = apply_calibration("overall_risk", 2, [], OVERALL, False, 0.6123, "HIGH")
    assert out == (2, "HIGH", 0.6123)


# ------------------------------------------------------------- enabled: shifts

def test_enabled_overall_risk_high_becomes_medium_and_p_top_follows():
    probs = [0.05, 0.25, 0.65, 0.05]
    idx, label, p_top = apply_calibration("overall_risk", 2, probs, OVERALL, True)
    assert (idx, label) == (1, "MEDIUM")
    assert p_top == probs[1] == 0.25


def test_enabled_capability_breadth_broad_becomes_moderate():
    probs = [0.05, 0.20, 0.70, 0.05]
    assert apply_calibration("capability_breadth", 2, probs, BREADTH, True) == \
        (1, "MODERATE", 0.20)


# ------------------------------------------------ enabled: UNKNOWN is sacred

def test_enabled_never_shifts_unknown_on_capability_breadth():
    """capability_breadth's ladder ENDS in UNKNOWN -- this case is live.

    A -1 here would rewrite an explicit "cannot determine" as BROAD, i.e.
    manufacture a positive finding out of an abstention.
    """
    probs = [0.05, 0.05, 0.20, 0.70]
    out = apply_calibration("capability_breadth", 3, probs, BREADTH, True)
    assert out == (3, "UNKNOWN", 0.70)
    assert out[1] == "UNKNOWN"


def test_unknown_author_is_also_never_shifted():
    assert "UNKNOWN" in NEVER_SHIFT_LABELS
    assert "UNKNOWN_AUTHOR" in NEVER_SHIFT_LABELS
    ladder = LADDERS["maintainer_trust"]
    i = ladder.index("UNKNOWN_AUTHOR")
    probs = [0.1] * len(ladder)
    probs[i] = 0.6
    # not in SHIFTS either, so doubly protected
    assert apply_calibration("maintainer_trust", i, probs, ladder, True) == \
        (i, "UNKNOWN_AUTHOR", 0.6)


# ------------------------------------------------------------------- clamping

def test_clamping_index_zero_stays_zero():
    probs = [0.8, 0.1, 0.05, 0.05]
    assert apply_calibration("overall_risk", 0, probs, OVERALL, True) == \
        (0, "LOW", 0.8)


def test_clamping_never_produces_an_out_of_range_index():
    for axis in SHIFTS:
        ladder = LADDERS[axis]
        probs = [1.0 / len(ladder)] * len(ladder)
        for i in range(len(ladder)):
            j, lbl, _p = apply_calibration(axis, i, probs, ladder, True)
            assert 0 <= j < len(ladder)
            assert lbl == ladder[j]


# ------------------------------------------------- axes outside SHIFTS untouched

@pytest.mark.parametrize("axis", ["data_sensitivity", "exploit_surface",
                                  "network_egress", "auth_strength",
                                  "maintainer_trust"])
def test_axis_not_in_shifts_is_untouched_even_when_enabled(axis):
    assert axis not in SHIFTS
    ladder = LADDERS[axis]
    probs = [0.1] * len(ladder)
    for i, lbl in enumerate(ladder):
        p = list(probs)
        p[i] = 0.5
        assert apply_calibration(axis, i, p, ladder, True) == (i, lbl, 0.5)


def test_unknown_axis_name_is_untouched():
    probs = [0.1, 0.9]
    assert apply_calibration("not_an_axis", 1, probs, ("A", "B"), True) == \
        (1, "B", 0.9)


# ------------------------------------------------------------- p_top coherence

@pytest.mark.parametrize("axis", sorted(LADDERS))
@pytest.mark.parametrize("enabled", [False, True])
def test_p_top_always_equals_probs_at_the_emitted_index(axis, enabled):
    ladder = LADDERS[axis]
    probs = [round(0.03 * (k + 1), 4) for k in range(len(ladder))]
    for i in range(len(ladder)):
        idx, _lbl, p_top = apply_calibration(axis, i, probs, ladder, enabled)
        assert p_top == probs[idx], (
            "{} idx {} enabled={} emitted p_top of a different label"
            .format(axis, i, enabled))


def test_short_probs_list_does_not_raise_and_keeps_the_given_p_top():
    out = apply_calibration("overall_risk", 2, [0.1], OVERALL, True, 0.55, "HIGH")
    assert out[0] == 1 and out[1] == "MEDIUM"
    assert out[2] == 0.55           # fell back rather than raising or nulling


def test_empty_probs_disabled_is_still_a_no_op():
    assert apply_calibration("overall_risk", 3, [], OVERALL, False, 0.9,
                             "CRITICAL") == (3, "CRITICAL", 0.9)


# ---------------------------------------------------------------- rule_version

def test_rule_version_v1_when_disabled_v2_when_enabled():
    assert rule_version(False) == RULE_V1 == "gate_rule_v1_2026-06-16"
    assert rule_version(True) == RULE_V2 == "gate_rule_v2_2026-09-03"


def test_rule_version_never_claims_v2_while_the_remap_is_off():
    assert "v2" not in rule_version(False)


def test_emitted_rule_matches_the_constant_weekly_rescore_used_before():
    """v1 string must be byte-identical to weekly_rescore.RULE, or every
    historical row would look like it came from a different rule."""
    src = (RESCORE / "weekly_rescore.py").read_text(encoding="utf-8")
    assert 'RULE = "gate_rule_v1_2026-06-16"' in src
    assert rule_version(False) == "gate_rule_v1_2026-06-16"


# ----------------------------------------------------------- the env-var flag

@pytest.mark.parametrize("val", ["1", "true", "TRUE", "True", "yes", "YES",
                                 " 1 ", "Yes"])
def test_calibration_enabled_true_values(monkeypatch, val):
    monkeypatch.setenv("ZO_CALIBRATION_V2", val)
    assert calibration_enabled() is True


@pytest.mark.parametrize("val", ["0", "", "no", "false", "off", "on", "2",
                                 "y", "enabled", "truthy"])
def test_calibration_enabled_false_values(monkeypatch, val):
    monkeypatch.setenv("ZO_CALIBRATION_V2", val)
    assert calibration_enabled() is False


def test_calibration_default_is_off_when_unset(monkeypatch):
    monkeypatch.delenv("ZO_CALIBRATION_V2", raising=False)
    assert calibration_enabled() is False
