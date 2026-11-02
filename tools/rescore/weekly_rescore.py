#!/usr/bin/env python3
"""weekly_rescore.py -- surgical, resumable, cost-capped registry rescore.

Chairman directive 2026-07-14: "run it and make the script surgical and precise
so we can run these jobs cheaply and scheduled every 7 days without loss."

Generalizes the PROVEN 2026-07-03 full-rescore path (fire_score.py +
vast_score_onstart.sh + import_preds_fly.py, manifest jobs/registry_rescore_v1.json)
into one resumable entrypoint with hard guards. Runs on the TOWER (needs:
flyctl authed, gh/git, AgentVault, psycopg2, vastai_sdk).

PHASES (state.json in the run dir; every phase idempotent, rerun resumes):
  preflight -> export -> bundle -> fire -> watch -> collect -> destroy ->
  import -> backfill -> postcheck

MODES:
  --delta (default): score (a) never-scored distinct-URL servers (the 20k-goal
      lane) + (b) a refresh cohort ordered DISTRUSTED-FIRST, then oldest-scored
      first, capped by --refresh-cap (default 20000). Weekly delta => every
      server refreshed <= ~4 weeks, newest_scored_at advances weekly, cost
      ~$0.3-0.6/run. "Distrusted" is derived every run from the moat's own
      label histogram via score_validity (see cohort_trust): known-garbage
      cohorts are the NEWEST timestamps, so pure age-ordering would sort them
      LAST and the cadence would never reach them.
  --full: every distinct-URL server (the 7/3 shape, ~66k, ~$1.2).

NO-LOSS INVARIANTS (enforced, run aborts loudly on breach):
  I1 scored_servers AFTER import >= BEFORE  (import only delete+reinserts
     servers present in preds; a bad preds file cannot shrink coverage).
  I2 preds servers >= 90% of exported servers, else no destroy-on-green and
     the import still proceeds for what arrived (partial results are ingested,
     never discarded) but the run reports DEGRADED.
  I3 forensics (onstart.log) are pulled BEFORE any destroy, success or fail.
  I4 instance is ALWAYS destroyed by the end of collect/destroy phases --
     wall-clock deadline + $ ceiling breached => forensics + destroy + ALERT.
  I5 adapter sha256 verified against the promoted pin before every launch.

COST GUARDS: MAX_DPH ceiling on offer selection; COST_CAP_USD hard ceiling
(estimated dph * elapsed); DEADLINE_MIN wall clock. All overridable per run.
--cap-usd (authority ceiling, red team F8): the caller's per-wave dollar grant.
ENFORCED by this job, not estimated by the proposer: spend-since-launch is
MEASURED from vast (credit at launch minus credit now, floored by the billed-dph
estimate) every watch poll; at >= cap the instance is destroyed and CAP_HIT is
printed. A cap that is only logged is not a cap.

Scheduling shape (two Claude scheduled tasks, mirrors the 7/3 fire+collect
pattern): weekly-rescore-fire runs `--phase fire-all`; weekly-rescore-collect
runs `--phase collect-all` ~4h later. Either phase re-invoked is a no-op if
already complete. `--run` does everything in one process (interactive use).
"""
from __future__ import annotations

import os as _sg_os, sys as _sg_sys
_sg_sys.path.insert(0, _sg_os.path.dirname(_sg_os.path.abspath(__file__)))
from spend_guard import scaled_budget, scaled_deadline_min  # FU-090 #1784

# FU-151: one shared credential path for every flyctl caller in this repo.
_sg_sys.path.insert(0, _sg_os.path.dirname(_sg_os.path.dirname(_sg_os.path.abspath(__file__))))
from fly_token import hydrate_fly_token  # noqa: E402

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- constants
HERE = Path(__file__).resolve().parent                  # tools/rescore/
RUNS_ROOT = Path(os.environ.get("RESCORE_RUNS_ROOT", r"D:\zo\runs\weekly_rescore"))
LEDGER = RUNS_ROOT / "ledger.jsonl"
# V06 lifecycle_changed priority queue (written by _tools/lifecycle_changed.py --detect).
# Unconsumed entries are prepended to the refresh cohort during ph_export so that
# servers with meaningful changes (description, url, cert/CA, verdict) are rescored
# before the normal distrusted/age cohort.  Additive-not-essential: absent or
# unreadable file is a silent no-op -- the harness falls back to normal cohort logic.
CHANGED_QUEUE = RUNS_ROOT / "changed_priority.jsonl"

# Promotion seam (2026-10-15, chairman-funded v3.4 full-corpus advisory rescore).
# Both default to the v3.0 pin, so the SCHEDULED weekly-delta lane -- which runs
# this file with NO env set -- is byte-for-byte unchanged and stays on v3.0. A
# v3.4 (or any future) wave is promoted by exporting RESCORE_MODEL_VERSION +
# RESCORE_ADAPTER_SHA (+ RESCORE_ADAPTER_DIR) for THAT invocation only. The two
# versions COEXIST in mcp_llm_axis_scores because every read/write here is keyed
# on model_version; the prod VERDICT still reads v3.0 until a separate chairman
# cutover. REVERT: unset the env or restore these two literals.
MODEL_VERSION = os.environ.get("RESCORE_MODEL_VERSION", "v3.0_40974559")
ADAPTER_SHA_PIN = os.environ.get(
    "RESCORE_ADAPTER_SHA",
    "bf842f5450347c19ac195ef32a7298a1579566a7b9b454a5381fda333e7198e3")
RULE = "gate_rule_v1_2026-06-16"
AXES = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"]

SFT_REPO = "rob531/zomesh-sentinel-sft"
# Promoted adapter source: local dir first (the score-job-* git branches are
# periodically cleaned; the 6/25 one vanished by 7/14). Sha-pinned either way.
ADAPTER_LOCAL_DIR = Path(os.environ.get("RESCORE_ADAPTER_DIR",
                                        r"D:\zo\runs\v3.0_40974559_FULL\final"))
FLY_PG_APP = "mcplookup-db"
PROXY_PORT = 15432
DSN_FILE = Path(os.environ.get("RESCORE_DSN_FILE", r"D:\zo\runs\rescore_20260703\_dsn.txt"))
FRESHNESS_URL = "https://mcprisky.io/freshness"
# FU-027: /freshness has a per-machine cache with a 600s TTL and a cold recompute
# measured in the 20-300s band, so a 30s read is BELOW the observed cold path and
# fails deterministically on a cache miss rather than occasionally. Budget past the
# cold path and retry -- a cache miss warms the cache, so attempt 2 is usually fast.
FRESHNESS_TIMEOUT = int(os.environ.get("RESCORE_FRESHNESS_TIMEOUT", "120"))
FRESHNESS_RETRIES = int(os.environ.get("RESCORE_FRESHNESS_RETRIES", "3"))
FRESHNESS_BACKOFF = float(os.environ.get("RESCORE_FRESHNESS_BACKOFF", "5"))

MAX_DPH_DEFAULT = 0.45
COST_CAP_DEFAULT = 3.00
DEADLINE_MIN_DEFAULT = 300
REFRESH_CAP_DEFAULT = 20000
GEO_BLOCK = ("CN", "HK", "TW", "RU", "IR", "KP")
IMAGE = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime"

SYS = (HERE / "prompt_system.txt").read_text(encoding="utf-8")
SIG = (HERE / "prompt_signals_block.txt").read_text(encoding="utf-8")
ONSTART = (HERE / "vast_score_onstart.sh").read_text(encoding="utf-8")
def _weekly_rescore_lib_exec(_seam):
    """Run weekly_rescore_lib/<seam>.py IN THIS MODULE'S NAMESPACE (chain rebase_godfiles S20).
    The seam files are weekly_rescore.py split behind its own name, not separate modules."""
    import importlib.machinery as _ilm
    _p = str(Path(__file__).resolve().parent / "weekly_rescore_lib" / (_seam + ".py"))
    if not os.path.exists(_p):
        raise ImportError("weekly_rescore.py: seam file missing: %s (chain rebase_godfiles S20; "
                          "git -C <repo> checkout -- tools/rescore/weekly_rescore_lib/ restores it)" % _p)
    _n = "weekly_rescore_lib_" + _seam
    exec(_ilm.SourceFileLoader(_n, _p).get_code(_n), globals())


# ==== seam 'core' -> weekly_rescore_lib/core.py (chain rebase_godfiles S20, 2026-11-02) ====
# utilities, Run dataclass, run state tracking and lifecycle management.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: weekly_rescore.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (14):
#   utcnow, log, ledger, secret, _driver_provenance, Run, _terminally_finished
#   open_run, _is_abort_event, _state_abandoned, _state_terminal, _parse_ts
#   open_runs, check_open_runs
_weekly_rescore_lib_exec("core")


# ---------------------------------------------------------------- fly proxy
_PROXY = None
# ==== seam 'preflight' -> weekly_rescore_lib/preflight.py (chain rebase_godfiles S20, 2026-11-02) ====
# Fly proxy setup, PostgreSQL connection, freshness checks, preflight phase.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: weekly_rescore.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (9):
#   ensure_proxy, pg_conn, freshness, freshness_safe, ph_preflight
#   _read_changed_priority, residue_verdict, snap_refresh_to_cohort, cohort_trust
_weekly_rescore_lib_exec("preflight")
# ==== seam 'phases' -> weekly_rescore_lib/phases.py (chain rebase_godfiles S20, 2026-11-02) ====
# export, bundle, fire, vast instance lifecycle, watch and collect phases.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: weekly_rescore.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (18):
#   ph_export, adapter_blob_size, ph_bundle, ph_fire, _results_state, _destroy
#   _eff_cost_cap, _eff_deadline, _eff_cap_usd, _vast_credit, _read_launch_credit
#   _spent_since_launch, _billed_dph, _instance_probe, _pod_progress, _watch_basis
#   _pull_instance_logs, ph_watch_collect
_weekly_rescore_lib_exec("phases")
# ==== seam 'complete' -> weekly_rescore_lib/complete.py (chain rebase_godfiles S20, 2026-11-02) ====
# import, backfill, postcheck phases, and main entrypoint.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: weekly_rescore.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (6):
#   ph_import, ph_backfill, refresh_yield, zero_yield_streak, ph_postcheck, main
_weekly_rescore_lib_exec("complete")


if __name__ == "__main__":
    main()
