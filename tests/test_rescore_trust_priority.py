"""FU-093 regression tests: the refresh lane must rank by TRUST, not just age.

Each scoring wave stamps ONE shared scored_at on every row it writes, so
scored_at IS the cohort key. Three cohorts in the moat are provably
random-head garbage and they carry the NEWEST timestamps:

    scored_at                     servers   verdict       collapsed
    2026-06-25 / 07-03 (tiny)     1-314     INSUFFICIENT  0/7
    2026-07-18 22:07:43.323514    66,050    DEGENERATE    6/7 RANDOM-HEADS
    2026-07-21 06:09:50.825458    65,045    DEGENERATE    6/7 RANDOM-HEADS
    2026-07-24 23:29:53.653616    125,731   DEGENERATE    7/7 RANDOM-HEADS
    2026-07-26 23:14:21.032938    20,576    VALID         0/7

`refresh_rows.sort(key=lambda r: scored_at[r[0]])` -- oldest first -- therefore
sorted the known garbage LAST, and the weekly cadence would never have reached
it. A garbage score is worse than no score: it is served to customers as
though it were real.

These tests pin (1) that the verdict is DERIVED from the moat's own histogram
rather than a hardcoded date list, (2) the resulting order, and (3) that a
failing trust query degrades to the historical ordering instead of blocking
the export.
"""
from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "rescore" / "weekly_rescore.py"

COHORT = {
    "tiny_0625": datetime(2026, 6, 25, 12, 0, 0),
    "bad_0718": datetime(2026, 7, 18, 22, 7, 43, 323514),
    "bad_0721": datetime(2026, 7, 21, 6, 9, 50, 825458),
    "bad_0724": datetime(2026, 7, 24, 23, 29, 53, 653616),
    "good_0726": datetime(2026, 7, 26, 23, 14, 21, 32938),
}


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _moat_rows() -> list[tuple]:
    """What `GROUP BY scored_at, axis_name, label` returns for the real moat."""
    hist = {
        # the three random-head waves: multiple axes collapsed at once
        "bad_0718": {"overall_risk": {"HIGH": 66049, "CRITICAL": 1},
                     "auth_strength": {"WEAK": 66040, "UNKNOWN": 10}},
        "bad_0721": {"overall_risk": {"CRITICAL": 65045},
                     "auth_strength": {"STRONG": 65040, "WEAK": 5}},
        "bad_0724": {"overall_risk": {"LOW": 125726, "CRITICAL": 5},
                     "auth_strength": {"WEAK": 125723, "UNKNOWN": 8}},
        # the FU-108 wave: real, discriminative output
        "good_0726": {"overall_risk": {"MEDIUM": 14103, "HIGH": 5002,
                                       "CRITICAL": 1017, "LOW": 454},
                      "maintainer_trust": {"UNKNOWN_AUTHOR": 19825,
                                           "ESTABLISHED": 746, "VERIFIED": 5}},
        # too small to judge -- INSUFFICIENT, and NOT the same thing as bad
        "tiny_0625": {"overall_risk": {"LOW": 4, "HIGH": 3}},
    }
    return [(COHORT[c], ax, lb, n)
            for c, axes in hist.items()
            for ax, labels in axes.items()
            for lb, n in labels.items()]


class _Cursor:
    def __init__(self, rows, seen):
        self._rows, self._seen = rows, seen

    def execute(self, sql, params=None):
        self._seen["sql"], self._seen["params"] = sql, params

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Conn:
    def __init__(self, rows):
        self.rows, self.seen, self.cursors = rows, {}, 0

    def cursor(self):
        self.cursors += 1
        return _Cursor(self.rows, self.seen)


def test_cohort_trust_derives_verdicts_from_the_moats_own_histogram(wr):
    conn = _Conn(_moat_rows())
    distrusted, verdicts = wr.cohort_trust(conn)

    assert verdicts[COHORT["bad_0718"]] == "DEGENERATE"
    assert verdicts[COHORT["bad_0721"]] == "DEGENERATE"
    assert verdicts[COHORT["bad_0724"]] == "DEGENERATE"
    assert verdicts[COHORT["good_0726"]] == "VALID"
    # a small cohort is unjudgeable, NOT condemned: it must not jump the queue
    assert verdicts[COHORT["tiny_0625"]] == "INSUFFICIENT"
    assert distrusted == {COHORT["bad_0718"], COHORT["bad_0721"],
                          COHORT["bad_0724"]}


def test_cohort_trust_is_exactly_one_query_parameterised_on_model_version(wr):
    """The Fly box is 1-vCPU burstable -- one aggregate, never one per cohort."""
    conn = _Conn(_moat_rows())
    wr.cohort_trust(conn)
    assert conn.cursors == 1
    sql = conn.seen["sql"].lower()
    assert "group by scored_at, axis_name, label" in sql
    assert conn.seen["params"] == (wr.MODEL_VERSION,)
    # the dates are NEVER hardcoded -- a date list rots the next time a wave
    # goes wrong, and keeps condemning cohorts that have since been rescored
    src = MODULE_PATH.read_text(encoding="utf-8")
    for stamp in ("2026-07-18 22:07", "2026-07-21 06:09", "2026-07-24 23:29"):
        assert stamp not in src


def test_distrusted_cohorts_sort_ahead_of_age(wr):
    distrusted, _ = wr.cohort_trust(_Conn(_moat_rows()))
    scored_at = {"s_tiny": COHORT["tiny_0625"], "s18": COHORT["bad_0718"],
                 "s21": COHORT["bad_0721"], "s24": COHORT["bad_0724"],
                 "s26": COHORT["good_0726"]}
    rows = [("s26",), ("s_tiny",), ("s24",), ("s18",), ("s21",)]

    rows.sort(key=lambda r: (scored_at[r[0]] not in distrusted, scored_at[r[0]]))

    # garbage first (oldest garbage leading), then everything else oldest-first
    assert [r[0] for r in rows] == ["s18", "s21", "s24", "s_tiny", "s26"]

    # the plain age sort -- what shipped before -- spends the (capped) refresh
    # budget on cohorts that are FINE before it ever reaches the garbage. In
    # prod the head of that queue is ~1,200 INSUFFICIENT servers from
    # 06-25..07-03, and the tail is 257K random-head rows.
    old = [("s26",), ("s_tiny",), ("s24",), ("s18",), ("s21",)]
    old.sort(key=lambda r: scored_at[r[0]])
    assert [r[0] for r in old] == ["s_tiny", "s18", "s21", "s24", "s26"]
    cap = 2                                     # cf. --refresh-cap
    assert [r[0] for r in old][:cap] == ["s_tiny", "s18"]    # 1 slot wasted
    assert [r[0] for r in rows][:cap] == ["s18", "s21"]      # all distrusted


def test_trust_failure_falls_back_to_the_historical_order(wr):
    """FAIL-SAFE: the trust audit must never be able to block an export."""
    scored_at = {"a": COHORT["bad_0724"], "b": COHORT["tiny_0625"],
                 "c": COHORT["good_0726"]}
    rows = [("a",), ("b",), ("c",)]
    distrusted: set = set()          # what the except branch leaves behind
    rows.sort(key=lambda r: (scored_at[r[0]] not in distrusted, scored_at[r[0]]))
    expected = sorted([("a",), ("b",), ("c",)], key=lambda r: scored_at[r[0]])
    assert rows == expected

    class _Boom:
        def cursor(self):
            raise RuntimeError("relation mcp_llm_axis_scores does not exist")

    with pytest.raises(RuntimeError):
        wr.cohort_trust(_Boom())     # ph_export catches this and logs it


def test_counts_gate_agrees_with_the_rows_gate(wr):
    """validate_run_from_histogram is the SAME code path, not a second copy."""
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "rescore"))
    from score_validity import validate_run, validate_run_from_histogram

    hist = {"overall_risk": {"LOW": 125726, "CRITICAL": 5},
            "auth_strength": {"WEAK": 125723, "UNKNOWN": 8}}
    rows = [{"axis_name": ax, "label": lb}
            for ax, h in hist.items() for lb, n in h.items() for _ in range(n)]
    assert validate_run_from_histogram(hist) == validate_run(rows)
    assert validate_run_from_histogram(hist)["random_head_signature"] is True
    # Counter input is accepted as-is (what cohort_trust would hand it)
    assert validate_run_from_histogram(
        {ax: Counter(h) for ax, h in hist.items()})["verdict"] == "DEGENERATE"
