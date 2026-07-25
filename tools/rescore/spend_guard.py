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
THROUGHPUT_RPH   = 19_200    # rows/hr (empirical 320 rows/min)
DEADLINE_K       = 1.5
D_MIN_MIN        = 45
D_ABS_MIN        = 18 * 60
MONTHLY_HARD     = 25.00     # $/month pool hard-halt
MONTHLY_ALERT    = 20.00
DB_MIN_FREE_FRAC = 0.15      # keep >=15% of the PG volume free after ingest


def scaled_budget(n_rows: int, r: float = R_FLOOR) -> float:
    """Cost cap = clamp(K*r*N, B_MIN, B_ABS). Fixed at EXPORT (n from the
    frozen distinct-row export count) so a runaway can't inflate its budget."""
    return min(B_ABS, max(B_MIN, K * r * max(0, n_rows)))


def scaled_deadline_min(n_rows: int) -> int:
    hrs = (max(0, n_rows) / THROUGHPUT_RPH) * DEADLINE_K
    return int(min(D_ABS_MIN, max(D_MIN_MIN, round(hrs * 60))))


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
    assert scaled_deadline_min(230_000) == 1078, scaled_deadline_min(230_000)  # ~18h
    assert scaled_deadline_min(10**7) == D_ABS_MIN           # huge N -> 18h clamp
    assert scaled_deadline_min(58_000) == 272, scaled_deadline_min(58_000)  # ~4.5h
    assert scaled_deadline_min(100) == D_MIN_MIN
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