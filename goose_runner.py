#!/usr/bin/env python3
"""
goose_runner.py - Goose Tier 1 Autonomous Builder Runner
Polls mesh_memory for low-complexity directives and executes them with Goose AI.
Falls back to MiniMax/zo_builder tier on Goose failure.
"""

import os
import sys
import time
import json
import subprocess
import threading
import re
import signal
import requests
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

sys.path.insert(0, "/home/workspace/zo_sentinel")  # ensure zo_sentinel package importable
from zo_sentinel.build_routing import (  # noqa: E402
    build_artifact_row, build_provenance_row, build_env_for, directive_content,
    resolve_directive_id, tier_for_complexity)
from zo_sentinel.build_completion import (  # noqa: E402
    MAX_GHOST_ATTEMPTS, bump_ghost, clear_ghost, declared_output, ghost_attempts,
    park_directive, unmet_requires,
    output_confirmed, failed_quarantined, workspace_diff_state)
from zo_sentinel.gates.hollow import hollow_scaffold_scan  # noqa: E402
from zo_sentinel import undeclared_write_guard  # noqa: E402  # GH #3415 fix 4
from zo_sentinel.build_lessons import (  # noqa: E402
    record_lesson, resolve_lessons, open_lessons_for, format_lessons_context)

# Phase-1 feedback edge (file-based only -- NO DB load; "zo_db_query destabilizes
# write_service" per the 2026-05-31 ops note). state_loopback lives beside this
# file; uv_gate_runner is the isolated Tier-0/1 gate.
import state_loopback as sl  # noqa: E402
import singleton_lock  # identity-verified single-instance lock
try:
    from tools.uv_gate_runner import run_gates  # noqa: E402
except Exception:  # tools/ not importable in some launch contexts -> gate becomes a no-op
    run_gates = None

# Spine gate (PR #10208 router): route an app-surface build directive to its canonical
# mountpoint BEFORE spending a builder token; REJECT_UNSPINED = no mounted app route.
# Deterministic/lexical (no network -- Clef is tower-local; zocompute uses the fallback).
# No-op if the router is absent; fail-open on any error.
try:
    from tools.spine_mount_router import classify_directive as _spine_classify, REJECT as _SPINE_REJECT  # noqa: E402
except Exception:
    _spine_classify, _SPINE_REJECT = None, "REJECT_UNSPINED"
_SPINE_APP_RECIPES = {"webapp_backend_fastapi", "webapp_frontend_react",
                      "webapp_fullstack", "service_dir_from_exemplar"}

# =============================================================================
# CONSTANTS
# =============================================================================

SERVICE_NAME = "goose_runner"
SERVICE_PORT = 8799
PID_FILE = Path(f"/tmp/{SERVICE_NAME}.pid")
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773"
NL_QUERY_URL = "http://127.0.0.1:8784"

POLL_SECS = 60
HEARTBEAT_INTERVAL = 30
GOOSE_TIMEOUT = 900   # headroom for the architect loop + delegate_to_builder codegen

# FU-017: builder-scoped goose BINARY (mirror of the architect's
# ZO_ARCHITECT_GOOSE_BIN). Default "goose" = the PATH binary (1.34.1 today).
# Point ZO_BUILDER_GOOSE_BIN at /usr/local/bin/goose-1.43 to flip ONLY the
# builder; unset to roll back. When non-default, run_goose_task gives the
# subprocess an ISOLATED config/data HOME -- version skew on the shared
# ~/.config/goose session store panics a different goose version (same scar
# the architect hit flipping to 1.38).
GOOSE_BIN = os.environ.get("ZO_BUILDER_GOOSE_BIN", "goose")

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOGS_DIR = Path("/home/workspace/logs")
try:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Import-safe: a read-only or absent /home/workspace (CI, unit tests, a
    # non-prod host) must not crash `import goose_runner`. The daemon re-ensures
    # its dirs at runtime (ensure_dirs()); this module-level call is best-effort.
    pass
LOG_FILE = LOGS_DIR / "goose_runner.log"

SHARED_OUTPUTS = Path("/home/workspace/shared/outputs/goose")
TASK_FILE = Path("/tmp/goose_task.txt")

DIRECTIVES_PATH = Path("/home/workspace/zo_sentinel/directives")
LESSONS_DIR = PROJECT_DIR / "lessons"   # file-based lessons index (zero DB load)
PENDING_DIR = DIRECTIVES_PATH / "pending"
DONE_DIR = DIRECTIVES_PATH / "done"
# Durable quarantine store OUTSIDE the repo tree, so `git clean` on daemon
# respawn/refresh cannot wipe .failed sentinels (council 2026-06-20).
DURABLE_QUARANTINE_DIR = Path("/home/workspace/zo_sentinel_state/quarantine")

# OpenAI API key (MiniMax is OpenAI-compatible)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_API_BASE = "https://api.minimax.chat/v1"
SHIM_URL = os.environ.get("GOOSE_CHAT_ENDPOINT", "http://127.0.0.1:8796/v1/chat/completions")
def _goose_runner_lib_exec(_seam):
    """Run goose_runner_lib/<seam>.py IN THIS MODULE'S NAMESPACE (chain rebase_godfiles S20).
    The seam files are goose_runner.py split behind its own name, not separate modules."""
    import importlib.machinery as _ilm
    _p = str(Path(__file__).resolve().parent / "goose_runner_lib" / (_seam + ".py"))
    if not os.path.exists(_p):
        raise ImportError("goose_runner.py: seam file missing: %s (chain rebase_godfiles S20; "
                          "git -C <repo> checkout -- goose_runner_lib/ restores it)" % _p)
    _n = "goose_runner_lib_" + _seam
    exec(_ilm.SourceFileLoader(_n, _p).get_code(_n), globals())


# ==== seam 'core' -> goose_runner_lib/core.py (chain rebase_godfiles S20, 2026-11-02) ====
# utility functions, heartbeat, ws I/O, directive management, goose eligibility check.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: goose_runner.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (17):
#   log, get_utc_now, send_heartbeat, heartbeat_loop, check_single_instance
#   remove_pid_file, signal_handler, ensure_directories, ws_query, ws_write
#   failure_matrix_cached, _parse_directive, prune_done_pending, topup_quarantine
#   park_orphan_chains_safe, load_directives_from_mesh, is_goose_eligible
_goose_runner_lib_exec("core")
# ==== seam 'context' -> goose_runner_lib/context.py (chain rebase_godfiles S20, 2026-11-02) ====
# task spec, build context, model roster, SOA/recipe helpers, task execution.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: goose_runner.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (26):
#   write_task_spec, check_goose_installed, _graph_context, _lessons_context
#   _model_roster, _data_access_context, _select_recipe, _soa_int_env
#   _sanitise_service_name, _soa_service_name, _fmt_cols, _soa_schema_excerpt
#   _schema_kl_cached, _schema_ground_context, _bus_table_context, _reads_context
#   _engine_task, _soa_service_spec, _soa_day_key, _soa_state_read
#   _soa_state_write, recipe_counter, _soa_budget_left, soa_params_or_none
#   recipe_required_params, run_goose_task
_goose_runner_lib_exec("context")
# ==== seam 'completion' -> goose_runner_lib/completion.py (chain rebase_godfiles S20, 2026-11-02) ====
# code cleanup, fallback routing, result writing, artifact recording, gate chain, routing.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: goose_runner.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (24):
#   _strip_code_fences, call_minimax_fallback, route_to_coworker, write_result
#   mark_directive_completed, _emit_build_artifact_for, _commit_build_for
#   _record_build_provenance, _schema_prm_gate, _no_hollow_gate, _selftest_gate
#   _raw_sql_lint, _selftest_feedback, _edit_diff_gate, _syntax_gate
#   _ensure_bandit, _security_advisory, _complete, _mark_directive_failed
#   gate_error_text, _write_raw_directive, _gate_chain, _ghost_or_fail
#   log_directive_routed
_goose_runner_lib_exec("completion")
# ==== seam 'runner' -> goose_runner_lib/runner.py (chain rebase_godfiles S20, 2026-11-02) ====
# main polling loop.
# The code that stood here moved VERBATIM and runs right here, in this module's
# namespace: goose_runner.<name>, __file__ and globals are unchanged. EDIT IT
# THERE. Names it defines (1):
#   run
_goose_runner_lib_exec("runner")

# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    run()