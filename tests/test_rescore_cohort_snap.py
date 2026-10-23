"""FU-175 regression: a --refresh-cap cut must not strand a residue the gate condemns.

Measured 2026-10-04: the 10-03 wave at --refresh-cap 140000 took 116811 of the
117916-server 2026-07-27 cohort. The 1105 rows left behind -- unchanged, VALID the
day before as part of the whole -- were judged DEGENERATE on their own by the very
cohort_trust gate ph_export ranks on, so DISTRUSTED_REMAINING went 0 -> 1105 with no
new score written. `snap_refresh_to_cohort` moves the cut to a cohort boundary when
(and only when) the gate itself says the residue would be distrusted.

Both poles are pinned: a cut that WOULD strand a condemned residue is moved, and a
cut whose residue the gate passes is left exactly where the operator put it.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "rescore" / "weekly_rescore.py"

A = datetime(2026, 7, 27, 16, 52, 57)
B = datetime(2026, 7, 30, 1, 14, 52)
C = datetime(2026, 8, 4, 7, 7, 30)


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(spec):
    """spec: [(cohort, n)] -> (refresh_rows ordered as the driver orders them, scored_at)."""
    rows, scored_at, sid = [], {}, 0
    for cohort, n in spec:
        for _ in range(n):
            sid += 1
            rows.append((sid, f"u{sid}", True))
            scored_at[sid] = cohort
    return rows, scored_at


def _judge(verdict):
    calls = []

    def judge(sids):
        calls.append(list(sids))
        return verdict
    judge.calls = calls
    return judge


def test_cut_on_boundary_is_untouched(wr):
    rows, sa = _rows([(A, 100), (B, 100)])
    judge = _judge("DEGENERATE")
    out, note = wr.snap_refresh_to_cohort(rows, 100, sa, judge)
    assert out == rows[:100]
    assert judge.calls == []          # nothing to judge: the cut is clean
    assert "boundary" in note


def test_cap_beyond_rows_is_untouched(wr):
    rows, sa = _rows([(A, 50)])
    out, _ = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("DEGENERATE"))
    assert out == rows


def test_distrusted_small_residue_extends_to_whole_cohort(wr):
    # the 2026-10-03 shape in miniature: cap cuts cohort A leaving a tail of 11
    rows, sa = _rows([(A, 111), (B, 200)])
    judge = _judge("DEGENERATE")
    out, note = wr.snap_refresh_to_cohort(rows, 100, sa, judge)
    assert len(out) == 111 and all(sa[r[0]] == A for r in out)
    assert judge.calls == [[r[0] for r in rows[100:111]]]   # judged exactly the residue
    assert "EXTENDED" in note


def test_passing_residue_leaves_operator_cap_alone(wr):
    # NEGATIVE CONTROL for the snap: same geometry, but the gate passes the residue
    rows, sa = _rows([(A, 111), (B, 200)])
    out, note = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("VALID"))
    assert out == rows[:100]
    assert "plain slice" in note


def test_insufficient_residue_is_not_treated_as_distrusted(wr):
    # FU-175: INSUFFICIENT is unevaluated, never folded into either trust bucket
    rows, sa = _rows([(A, 111), (B, 200)])
    out, _ = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("INSUFFICIENT"))
    assert out == rows[:100]


def test_large_distrusted_residue_retreats_to_cohort_start(wr):
    # cut inside B with a residue of 60 > 25% of cap 100 -> retreat to end of A
    rows, sa = _rows([(A, 70), (B, 90), (C, 10)])
    out, note = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("DEGENERATE"))
    assert len(out) == 70 and all(sa[r[0]] == A for r in out)
    assert "RETREATED" in note


def test_retreat_that_would_empty_the_lane_falls_back_with_warning(wr):
    # FU-181: an unpadded wave aborts, so never retreat to zero -- warn instead
    rows, sa = _rows([(A, 300)])
    out, note = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("DEGENERATE"))
    assert out == rows[:100]
    assert "RESIDUE_WARNING" in note


def test_extension_is_bounded_by_overshoot(wr):
    rows, sa = _rows([(A, 124), (B, 50)])
    # residue 24 <= 25% of 100 -> extend
    out, _ = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("DEGENERATE"))
    assert len(out) == 124
    rows, sa = _rows([(A, 126), (B, 50)])
    # residue 26 > 25 -> cannot extend; retreating empties the lane -> warning
    out, note = wr.snap_refresh_to_cohort(rows, 100, sa, _judge("DEGENERATE"))
    assert len(out) == 100 and "RESIDUE_WARNING" in note


def test_residue_verdict_uses_the_same_gate(wr):
    """residue_verdict feeds the histogram to score_validity, not a private threshold."""
    class Cur:
        def __init__(self):
            self.q = None

        def execute(self, q, params):
            self.q, self.params = q, params

        def fetchall(self):
            # single-class on 2 axes with >= MIN_COHORT rows -> DEGENERATE by the real gate
            return [("overall_risk", "MEDIUM", 600), ("auth_strength", "MODERATE", 600),
                    ("capability_breadth", "NARROW", 300), ("capability_breadth", "BROAD", 300)]

        def close(self):
            pass

    class Conn:
        def __init__(self):
            self.c = Cur()

        def cursor(self):
            return self.c

    conn = Conn()
    v = wr.residue_verdict(conn, [1, 2, 3])
    assert v == "DEGENERATE"
    assert conn.c.params[1] == [1, 2, 3]      # the residue ids reached the query
