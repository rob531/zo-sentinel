#!/usr/bin/env python3
"""spend_guard.py -- size-scaled spend guard for paid vast.ai GPU jobs.

Replaces the flat COST_CAP/DEADLINE constants with budgets that SCALE with
job size, a size-invariant live efficiency metric (CER), a cumulative churn
cap, and two MANDATORY pre-fire gates. Ratified by Council of Claudes
2026-07-24 (3 seats + FATHER); every amendment folded in. See FU-090.

Why: history shows the money bleed is provisioning churn + a flat cap that
guillotines a legitimately-big job and strands its partial spend -- NOT
correctly-sized jobs. So: budget scales linearly with size; the GUARD is a
normalized ratio (spend-per-scored-row / expected), which is size-invariant;
"waste" == spend that is not buying scored rows (the wedge case).

Anchors (two independent derivations agree => trustworthy):
  empirical  : $1.19 / 65,045 rows       = 1.83e-5 $/row
  gpu-econ   : $0.35/hr / 19,200 rows/hr = 1.82e-5 $/row
"""
from __future__ import annotations
import zlib

# ---- pinned constants (CofC 2026-07-24) --------------------------------
R_FLOOR          = 1.83e-5   # $/row. FLOOR. refit DOWNWARD only, CLEAN runs only.
K                = 1.5       # budget headroom (price + variance)
B_MIN            = 0.50      # floor cap for tiny deltas
B_ABS            = 10.00     # chairman absolute backstop -- never exceed w/o chairman
CER_MAX          = 2.0       # trip threshold (size-invariant)
ROWS_FLOOR_FRAC  = 0.25      # CER cannot trip until >= 25% of N scored ...
ROWS_FLOOR_ABS   = 10_000    # ... or >= 10k rows, whichever is larger
CHURN_K          = 1.5       # cumulative-refire budget = CHURN_K * B(N)
THROUGHPUT_RPH   = 19_200    # rows/hr (empirical 320 rows/min) -- $/row econ anchor
PLAN_RPH         = 14_000    # rows/hr PLANNING rate = SLOWEST observed clean run
                             # (65,045 rows / 275.4 min). Observed clean rates span
                             # 14.2k-27.1k/hr, ~2x. Plan with the slow end, not the mean.
STARTUP_MIN      = 45        # FIXED pre-row-1 cost: vast provisioning + docker pull +
                             # HF prefetch of Qwen2.5-3B (~6GB) + model load. Measured
                             # >=34.8 min on run 20260725-182808 (and still not done).
DEADLINE_K       = 1.5
D_MIN_MIN        = 90        # was 45 -- guillotined run 20260725-182808 mid-startup
D_ABS_MIN        = 18 * 60
MONTHLY_HARD     = 25.00     # $/month pool hard-halt
MONTHLY_ALERT    = 20.00
DB_MIN_FREE_FRAC = 0.15      # keep >=15% of the PG volume free after ingest


WAVE_CEILING_USD = 3.00  # authority envelope, per wave. HARD clamp (FU-342).
MAX_DPH_CEILING = 0.45   # == weekly_rescore.MAX_DPH_DEFAULT, the offer filter


def scaled_budget(n_rows: int, r: float = R_FLOOR) -> float:
    """Cost cap, DERIVED FROM THE DEADLINE so the money guard can never fire
    before the wedge guard (FU-342).

    The old form was clamp(K*r*N, B_MIN, B_ABS) -- linear through origin, no
    startup term. But `ph_watch_collect` compares it against `elapsed_h * dph`
    measured from `fired_at`, so the cap is ALSO a wall-clock deadline worth
    budget/dph*60 minutes. At the observed dph it was worth 134m against a 199m
    `scaled_deadline_min`, so the deadline could never fire and the 45-minute
    STARTUP_MIN allowance FU-104 added -- for provisioning, image pull and the
    6GB model load -- was unfunded. Wave 20260811-063956 was destroyed at 134m
    with `collected: []`, 65 minutes before its own deadline allowed.

    Deriving the cap from the deadline at the OFFER CEILING makes the two
    consistent by construction rather than by two constants agreeing by luck.
    Still fixed at EXPORT: MAX_DPH_CEILING is a constant and n_rows is frozen,
    so a runaway still cannot inflate its own budget.

    The WAVE_CEILING_USD clamp LOWERS the effective ceiling -- the previous
    B_ABS of $10 let this function return $3.29 at N=120k, above the authority
    envelope's $3/wave. It can no longer do that.
    """
    minutes = scaled_deadline_min(n_rows)
    derived = minutes / 60.0 * MAX_DPH_CEILING
    return min(B_ABS, WAVE_CEILING_USD, max(B_MIN, derived))


def scaled_deadline_min(n_rows: int) -> int:
    """Wall-clock deadline = FIXED startup allowance + K * scoring time at the
    SLOWEST observed clean rate. AFFINE, not linear-through-origin.

    FU-104. The old model was pure throughput (N/rate) with a 45m floor, which
    implicitly asserted that a small cohort is a FAST one. It is not: a 3,576-row
    cohort is CHEAP but still pays the full fixed cost of provisioning a pod,
    pulling the image, prefetching the 6GB base model and loading it before row 1
    is scored. Run 20260725-182808 was handed deadline=45m, spent ~35m of it on
    startup, breached at exactly 45m, collected NOTHING and self-destroyed.

    Separation of concerns: the COST CAP (scaled_budget) is the money guard and
    the CER is the efficiency guard. The deadline is only a WEDGE guard -- so it
    should be generous. A deadline tighter than the work is a self-inflicted
    zero-yield burn; a loose one costs nothing extra because cap+CER still bind.
    """
    scoring_min = (max(0, n_rows) / PLAN_RPH) * 60.0 * DEADLINE_K
    return int(min(D_ABS_MIN, max(D_MIN_MIN, round(STARTUP_MIN + scoring_min))))


def cer(spend_usd: float, rows_scored: int, r: float = R_FLOOR) -> float:
    """Cost-Efficiency Ratio = actual $ / expected $ for work delivered.
    ~1.0 healthy; >>1 spending faster than delivering; inf if rows_scored=0."""
    if rows_scored <= 0:
        return float("inf")
    return spend_usd / (rows_scored * r)


def cer_floor_reached(n_rows: int, rows_scored: int) -> bool:
    return rows_scored >= max(ROWS_FLOOR_ABS, int(ROWS_FLOOR_FRAC * n_rows))


def cer_trips(commit_cers, n_rows: int, rows_scored: int) -> bool:
    """Trip ONLY at checkpoint-commit boundaries: >=2 consecutive commits
    over CER_MAX, and only once the rows floor is reached (kills small-
    denominator noise + the sawtooth false-positive)."""
    if not cer_floor_reached(n_rows, rows_scored):
        return False
    if len(commit_cers) < 2:
        return False
    return commit_cers[-1] > CER_MAX and commit_cers[-2] > CER_MAX


def churn_budget(n_rows: int, r: float = R_FLOOR) -> float:
    return min(B_ABS, CHURN_K * scaled_budget(n_rows, r))


def refit_r_downward(current_r: float, observed_r: float, clean_run: bool) -> float:
    """r is a FLOOR reference: only ever LOWERED, and only from a CLEAN
    (completed, non-degraded) run. A wedged/degraded run (high $/low rows)
    can never ratchet r -- and thus budgets -- upward."""
    if clean_run and observed_r < current_r:
        return observed_r
    return current_r


def shard_of(server_id: str, n_shards: int) -> int:
    """Deterministic, frozen disjoint partition. The export SQL MUST mirror
    this expression so cohorts never overlap or race across resumes."""
    return zlib.crc32(str(server_id).encode()) % n_shards


def db_fire_gate(projected_add_rows: int, bytes_per_row: float,
                 db_size_bytes: int, volume_bytes: int) -> dict:
    """GATE 1: refuse to fire unless the projected ingest leaves >= DB_MIN_FREE_FRAC free."""
    projected = db_size_bytes + bytes_per_row * projected_add_rows
    free_frac = 1.0 - projected / volume_bytes
    return {"gate": "db_disk", "pass": free_frac >= DB_MIN_FREE_FRAC,
            "projected_gb": round(projected / 1e9, 2),
            "volume_gb": round(volume_bytes / 1e9, 2),
            "free_frac_after": round(free_frac, 3)}


def monthly_fire_gate(mtd_spend: float, job_budget: float) -> dict:
    """GATE 2: month-to-date + this job's budget must stay under the pool."""
    projected = mtd_spend + job_budget
    return {"gate": "monthly", "pass": projected <= MONTHLY_HARD,
            "alert": projected >= MONTHLY_ALERT,
            "mtd": round(mtd_spend, 2), "projected": round(projected, 2),
            "hard": MONTHLY_HARD}


def fire_allowed(db_gate: dict, monthly_gate: dict) -> bool:
    """BOTH gates must pass before ANY spend. If DB gate fails: DO NOT FIRE,
    surface the disk-bump decision to the chairman (never strand a paid result)."""
    return bool(db_gate["pass"] and monthly_gate["pass"])


if __name__ == "__main__":
    # ---- budget scaling ----
    assert abs(scaled_budget(230_000) - 6.3135) < 1e-3, scaled_budget(230_000)
    assert scaled_budget(500) == B_MIN            # tiny delta -> floor
    assert scaled_budget(10**9) == B_ABS          # runaway -> absolute backstop
    assert abs(scaled_budget(58_000) - 1.5921) < 1e-3, scaled_budget(58_000)
    # ---- deadline scaling ----
    assert scaled_deadline_min(230_000) == D_ABS_MIN, scaled_deadline_min(230_000)
    assert scaled_deadline_min(10**7) == D_ABS_MIN           # huge N -> 18h clamp
    assert scaled_deadline_min(58_000) == 418, scaled_deadline_min(58_000)   # ~7h
    assert scaled_deadline_min(100) == D_MIN_MIN
    # REGRESSION (FU-104): run 20260725-182808, N=3576, got 45m under the old
    # pure-throughput model, burned ~35m on startup, breached, collected [].
    assert scaled_deadline_min(3576) == 90, scaled_deadline_min(3576)
    # deadline must ALWAYS clear the fixed startup cost, even at N=0
    assert scaled_deadline_min(0) >= STARTUP_MIN
    # monotonic in N
    assert all(scaled_deadline_min(a) <= scaled_deadline_min(b)
               for a, b in [(0, 100), (100, 3576), (3576, 58_000), (58_000, 10**7)])
    # every CLEAN historical run must fit inside the deadline it would now get
    for n, observed_min in [(20_000, 64.1), (65_045, 275.4), (171_050, 432.0),
                            (125_731, 278.0)]:
        assert scaled_deadline_min(n) >= observed_min, (n, scaled_deadline_min(n))
    # COHERENCE: at nominal $/hr the deadline must not outlive the cost cap's
    # runway by much -- the two guards must not contradict each other.
    _DPH = 0.30
    assert scaled_deadline_min(3576) <= (scaled_budget(3576) / _DPH) * 60 + 1
    # ---- CER: size-invariant, healthy ~1 ----
    assert abs(cer(4.20, 230_000) - 0.997) < 0.01, cer(4.20, 230_000)
    assert cer(1.0, 0) == float("inf")            # wedge -> inf
    # ---- CER trip logic: commit-boundary + rows floor ----
    assert cer_trips([3.0, 3.0], 100_000, 60_000) is True     # 2 bad commits, floor met
    assert cer_trips([3.0], 100_000, 60_000) is False         # 1 commit -> no trip
    assert cer_trips([3.0, 3.0], 100_000, 5_000) is False     # rows floor not reached
    assert cer_trips([1.0, 1.1], 100_000, 60_000) is False    # healthy
    # ---- churn cap ----
    assert abs(churn_budget(58_000) - 2.388) < 1e-3, churn_budget(58_000)
    assert churn_budget(10**9) == B_ABS
    # ---- r refit: downward-only, clean-only ----
    assert refit_r_downward(1.83e-5, 1.5e-5, True) == 1.5e-5   # clean + lower -> adopt
    assert refit_r_downward(1.83e-5, 3.0e-5, True) == 1.83e-5  # higher -> ignore (no ratchet)
    assert refit_r_downward(1.83e-5, 1.0e-5, False) == 1.83e-5 # degraded -> ignore
    # ---- frozen partition: deterministic + disjoint ----
    ids = [f"srv-{i}" for i in range(4000)]
    parts = [shard_of(s, 4) for s in ids]
    assert set(parts) <= {0,1,2,3} and all(shard_of(s,4)==shard_of(s,4) for s in ids)
    # ---- fire gates ----
    dbg = db_fire_gate(1_610_000, 857, int(2.30e9), int(20e9))
    assert dbg["pass"] is True and dbg["projected_gb"] < 4.0, dbg   # 230k wave PASSES
    dbg_full = db_fire_gate(1_610_000, 857, int(18.0e9), int(20e9))
    assert dbg_full["pass"] is False                                # near-full volume REFUSES
    mg = monthly_fire_gate(0.0, 6.31)
    assert mg["pass"] and not mg["alert"]
    assert monthly_fire_gate(24.0, 6.31)["pass"] is False           # would breach $25 -> refuse
    assert fire_allowed(dbg, mg) is True
    assert fire_allowed(dbg_full, mg) is False
    print("PASS spend_guard self-tests")