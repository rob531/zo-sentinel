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
    output_confirmed, failed_quarantined)
from zo_sentinel.gates.hollow import hollow_scaffold_scan  # noqa: E402
from zo_sentinel.build_lessons import (  # noqa: E402
    record_lesson, resolve_lessons, open_lessons_for, format_lessons_context)

# Phase-1 feedback edge (file-based only -- NO DB load; "zo_db_query destabilizes
# write_service" per the 2026-05-31 ops note). state_loopback lives beside this
# file; uv_gate_runner is the isolated Tier-0/1 gate.
import state_loopback as sl  # noqa: E402
try:
    from tools.uv_gate_runner import run_gates  # noqa: E402
except Exception:  # tools/ not importable in some launch contexts -> gate becomes a no-op
    run_gates = None

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

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOGS_DIR = Path("/home/workspace/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
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
SHIM_URL = "http://127.0.0.1:8796/v1/chat/completions"

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def log(msg):
    """Write to log file.

    NOTE: supervisord captures this daemon's stdout into the same log file
    via stdout_logfile=/home/workspace/logs/goose_runner.log. If we ALSO
    print(), every line lands twice with identical microsecond timestamps.
    We keep the explicit file write (works whether or not supervisord is in
    play) and drop the print. For ad-hoc manual runs, tail the log file.
    """
    ts = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def get_utc_now():
    return datetime.now(timezone.utc).isoformat()

def send_heartbeat():
    """Send heartbeat to service_health table."""
    try:
        payload = {
            "table": "service_health",
            "rows": [{
                "service": SERVICE_NAME,
                "last_heartbeat": get_utc_now()
            }]
        }
        requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=5)
    except Exception as e:
        log(f"Heartbeat failed: {e}")

def heartbeat_loop():
    """Background heartbeat thread."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def check_single_instance():
    """Ensure only one instance runs."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            log(f"Instance already running with PID {pid} - exiting")
            sys.exit(0)
        except (ValueError, OSError):
            pass
    PID_FILE.write_text(str(os.getpid()))
    log(f"PID written: {os.getpid()}")

def remove_pid_file():
    """Remove PID file on shutdown."""
    if PID_FILE.exists():
        PID_FILE.unlink()
        log("PID file removed")

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    log(f"Received signal {signum} - shutting down")
    remove_pid_file()
    sys.exit(0)

def ensure_directories():
    """Create required directories."""
    SHARED_OUTPUTS.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    DURABLE_QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

def ws_query(sql):
    """Query write service."""
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query failed: {e}")
        return {"rows": [], "count": 0}

def ws_write(table, rows):
    """Write to write service."""
    try:
        payload = {"table": table, "rows": rows if isinstance(rows, list) else [rows], "wait": True}
        resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Write failed: {e}")
        return {"ok": False}


_MATRIX_CACHE = {"ts": 0.0, "rows": []}


def failure_matrix_cached(ttl=300):
    """failure_matrix rows for matrix-driven routing, cached (>= ttl secs) so we hit
    the bus at most once per few minutes -- NOT per directive (write_service is a
    single connection; zo_db_query under load destabilizes it). Best-effort -> []."""
    now = time.time()
    if _MATRIX_CACHE["rows"] and now - _MATRIX_CACHE["ts"] < ttl:
        return _MATRIX_CACHE["rows"]
    res = ws_query("SELECT directive_type, complexity, model, attempts, success_pct "
                   "FROM failure_matrix")
    rows = res.get("rows", []) if isinstance(res, dict) else []
    _MATRIX_CACHE["ts"] = now
    if rows:
        _MATRIX_CACHE["rows"] = rows
    return _MATRIX_CACHE["rows"]

# =============================================================================
# DIRECTIVE LOADING
# =============================================================================

def _parse_directive(d):
    """Normalise a directive: hoist nested content fields to top level."""
    if isinstance(d.get("content"), str):
        try:
            inner = json.loads(d["content"])
            if isinstance(inner, dict):
                # hoist complexity, description, spec if missing at top level
                for k in ("complexity", "description", "spec", "title", "key", "task"):
                    if k not in d and k in inner:
                        d[k] = inner[k]
                # use spec as content if richer
                if "spec" in inner and len(inner["spec"]) > len(d.get("content", "")):
                    d["content"] = inner["spec"]
        except Exception:
            pass
    # ensure directive_id exists (falls back to `task` so generator directives
    # don't all collapse to "unknown" and get skipped as already-built)
    if not d.get("directive_id"):
        d["directive_id"] = resolve_directive_id(d)
    return d


def prune_done_pending():
    """Move completed directives out of pending/ so the loader stops re-scanning
    a growing pile every cycle (the "skips too high" symptom).

    The generator writes gen_{key}_{task}.json, but mark_directive_completed only
    moves {directive_id}.json -- so completed gen_* files accumulate in pending/
    forever and are re-loaded + skipped each cycle. This relocates any pending
    file whose `<directive_id>.done.json` sentinel already exists into done/."""
    if not PENDING_DIR.exists():
        return 0
    moved = 0
    for f in PENDING_DIR.glob("*.json"):
        if f.name.endswith(".done.json") or f.name.endswith(".failed.json"):
            continue
        try:
            d = json.loads(f.read_text())
            if not isinstance(d, dict):
                continue
            did = resolve_directive_id(d)
        except Exception:
            continue
        if did and did != "unknown" and (DIRECTIVES_PATH / f"{did}.done.json").exists():
            try:
                DONE_DIR.mkdir(parents=True, exist_ok=True)
                f.rename(DONE_DIR / f.name)
                moved += 1
            except Exception:
                pass
    if moved:
        log(f"Pruned {moved} completed directive(s) from pending/ -> done/")
    return moved


def load_directives_from_mesh():
    """Load directives from BOTH mesh_memory DB and pending dir (merged)."""
    prune_done_pending()   # keep pending/ from growing unbounded with done files
    directives = []
    seen_ids = set()

    # Source 1: mesh_memory DB
    try:
        result = ws_query("""
            SELECT id, content, agent_id, memory_type, importance, created_at
            FROM mesh_memory
            WHERE agent_id = 'zo_sentinel.directive'
            AND memory_type = 'build_directive'
            ORDER BY importance DESC, created_at ASC
            LIMIT 20
        """)
        rows = result.get("rows", []) if result else []
        for d in rows:
            d = _parse_directive(d)
            did = str(d.get("directive_id", d.get("id", "")))
            if did not in seen_ids:
                seen_ids.add(did)
                directives.append(d)
        if rows:
            log(f"DB: {len(rows)} directives from mesh_memory")
    except Exception as e:
        log(f"DB query failed: {e}")

    # Source 2: pending directory (always scan, merge)
    if PENDING_DIR.exists():
        for f in sorted(PENDING_DIR.glob("*.json")):
            try:
                raw = f.read_text().strip()
                if not raw:
                    continue
                d = json.loads(raw)
                if not isinstance(d, dict):
                    continue
                # Resolve the build spec across producer field names (generator
                # directives carry it in `description`, not content/goal/spec).
                content = directive_content(d)
                if not content:
                    continue
                d["content"] = content
                d = _parse_directive(d)
                # If the body carried no usable id/task, fall back to the filename
                # (gen_<hash>_<task>) so the directive keeps a STABLE UNIQUE id
                # instead of collapsing to "unknown" and colliding on a shared
                # unknown.done.json sentinel -- which silently masks every id-less
                # generator directive as already-built (it never reaches the build).
                if d.get("directive_id") in (None, "", "unknown"):
                    d["directive_id"] = f.stem
                did = str(d.get("directive_id", f.stem))
                if did not in seen_ids:
                    seen_ids.add(did)
                    directives.append(d)
            except Exception as e:
                log(f"Failed to read {f.name}: {e}")

    log(f"Total directives loaded: {len(directives)} (DB + pending dir merged)")
    return directives

def is_goose_eligible(directive):
    """Check if directive is eligible for Goose execution.

    Accept all complexities (Goose/fallback handle any). Skip only when a
    sentinel says we're finished with it: `.done.json` (built -- verified by
    output_confirmed at completion time) or `.failed.json` (gave up after
    repeated ghost builds; surfaced, so don't churn). high-complexity coworker
    handoff is decided in the main loop, not here.
    """
    directive_id_val = directive.get("directive_id") or directive.get("id", "unknown")
    sentinels = Path("/home/workspace/zo_sentinel/directives")
    done = sentinels / f"{directive_id_val}.done.json"
    if done.exists():
        # SELF-HEAL stale ghost .done: a sentinel claiming completion whose declared
        # output file is absent on disk (output_confirmed False => there IS a declared
        # output and it's missing). The "completion" was a ghost, or the file was
        # quarantined/deleted afterward. Delete the stale sentinel and re-admit the
        # directive instead of skipping it forever. Folds tools/sweep_ghost_done.py
        # into the live loop -- no manual rm, no zm go bloat. NOTE: .failed is NOT
        # self-healed (a genuine give-up; auto-retrying would loop on unbuildable
        # directives -- clear those deliberately on a builder upgrade).
        if output_confirmed(directive):
            return False
        try:
            done.unlink()
            log(f"[self-heal] stale ghost .done for {directive_id_val} "
                f"(declared output {declared_output(directive)} absent) -- re-admitting")
        except OSError:
            return False   # couldn't remove -> leave skipped, don't churn
    # Durable-aware: a quarantine in EITHER the in-repo directives/ path OR the
    # durable store (outside the git tree) parks the directive. The durable copy
    # survives `git clean` on respawn, so a quarantine no longer evaporates.
    if failed_quarantined(directive_id_val, sentinels, DURABLE_QUARANTINE_DIR):
        return False
    # Subtractive, flag-gated dedup (ZO_DEDUP_REBUILD): a "create" directive whose
    # declared output ALREADY exists on disk with NO open lesson is a redundant
    # rebuild of a target we already have -- skip it to cut architect churn. This
    # NEVER touches generation (so it cannot cause the +0 / funnel-fork jams);
    # worst case it skips a rebuild (visible idle) and is reversible by unsetting
    # the flag. Edit-class directives are unaffected (declared_output -> None).
    # Toggle via env ZO_DEDUP_REBUILD, OR a sentinel file (operable without a
    # daemon env change / go.sh edit): directives/.dedup_rebuild_on containing "1".
    # Read fresh each call so it can be flipped on/off live, no restart.
    # Resolved through the declarative policy layer (zo_sentinel.policy: env >
    # durable override > legacy sentinel > policy_defaults.toml), read fresh
    # each call. Fail-open FALLBACK: the original inline env+sentinel logic,
    # so a policy fault degrades to prior behavior -- never to a crash.
    try:
        from zo_sentinel import policy as _policy
        _dedup_on = _policy.flag("queue.dedup_rebuild",
                                 directives_root=DIRECTIVES_PATH)
    except Exception:
        _dedup_on = bool(os.environ.get("ZO_DEDUP_REBUILD"))
        if not _dedup_on:
            try:
                _sf = DIRECTIVES_PATH / ".dedup_rebuild_on"
                _dedup_on = _sf.is_file() and _sf.read_text(encoding="utf-8").strip() not in ("", "0", "off", "false")
            except Exception:
                _dedup_on = False
    if _dedup_on:
        try:
            _out = declared_output(directive)
            if (_out is not None and _out.is_file()
                    and not open_lessons_for(LESSONS_DIR, _out.name)):
                log(f"[dedup] {directive_id_val}: output {_out.name} already exists "
                    f"(no open lesson) -- skipping redundant rebuild")
                return False
        except Exception:
            pass
    return True

# =============================================================================
# GOOSE EXECUTION
# =============================================================================

def write_task_spec(directive):
    """Write directive spec to task file for Goose."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown_" + str(time.time()))
    content = directive.get("content", directive.get("description", ""))
    
    task_spec = {
        "directive_id": directive_id,
        "description": directive.get("description", content[:200]),
        "content": content,
        "complexity": directive.get("complexity", "low"),
        "source": "goose_tier1",
        "created_at": get_utc_now()
    }
    
    TASK_FILE.write_text(json.dumps(task_spec, indent=2))
    log(f"Task spec written to {TASK_FILE}")
    return directive_id

def check_goose_installed():
    """Check if Goose CLI is available."""
    try:
        result = subprocess.run(
            ["goose", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            log(f"Goose version: {result.stdout.strip()}")
            return True
    except FileNotFoundError:
        log("Goose CLI not found in PATH")
    except Exception as e:
        log(f"Goose check failed: {e}")
    return False

def _graph_context(directive):
    """Best-effort code-graph neighborhood for the directive's target file,
    folded into the task so even a weak rung that ignores the graph_neighbors
    tool still gets structure. Pure read via ws_query (the architect's DuckDB
    copy of the graph). Returns '' on ANY error -- e.g. code_nodes not seeded --
    so it can never block or fail a build. No new dependency, no DB write."""
    try:
        out = declared_output(directive)
        if out is None:
            return ""
        fname = str(out.name).replace("'", "''")   # basename; escape quotes (ws_query has no params)
        if not fname:
            return ""
        res = ws_query(
            "SELECT DISTINCT e.relation AS rel, n1.label AS who, n1.source_file AS file "
            "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
            f"WHERE n2.source_file LIKE '%{fname}' "
            "AND e.relation IN ('calls','imports','imports_from','uses','inherits') LIMIT 20")
        rows = res.get("rows", []) if isinstance(res, dict) else []
        if not rows:
            return ""
        lines = [f"  - {r.get('who')} ({r.get('file')}) {r.get('rel')} it" for r in rows]
        return ("GRAPH CONTEXT -- existing code that depends on " + str(out.name) +
                " (do NOT break these contracts):\n" + "\n".join(lines))
    except Exception:
        return ""


def _lessons_context(directive):
    """Closed-loop READER: fold any OPEN lesson(s) for this directive's target into
    the build task so goose sees WHY a prior attempt ghosted and fixes the root
    cause instead of repeating it. Sits beside _graph_context in build-task
    assembly. File-based (open_lessons_for) -> zero DB load on the build hot path
    (the 'zo_db_query destabilizes write_service' rule). Returns '' on any miss so
    it can never block a build."""
    try:
        out = declared_output(directive)
        subject = (out.name if out is not None
                   else (directive.get("directive_id") or directive.get("id") or ""))
        if not subject:
            return ""
        # PRIMARY: functional / schema lessons -- these BLOCK and must be fixed first.
        primary = format_lessons_context(open_lessons_for(LESSONS_DIR, subject), subject)
        # SECONDARY: a non-blocking security advisory, appended LAST so functionality
        # and schema-binding lead the model's attention (security is the 2nd goal).
        sec = open_lessons_for(LESSONS_DIR, f"{subject}::security")
        sec_txt = ""
        if sec:
            _obs = str(sec[0].get("observation", ""))[:300]
            sec_txt = ("\n\nSECONDARY (security advisory, NON-BLOCKING -- do not sacrifice "
                       "the working feature or schema-binding to satisfy this): " + _obs)
        return (primary + sec_txt).strip()
    except Exception:
        return ""


_DATA_ACCESS_CTX = None


def _data_access_context(directive):
    """Proactive DATA-ACCESS grounding folded into EVERY build task (beside
    _graph_context / _lessons_context). MCP scores, signals, verdicts and the server
    registry live in the DATABASE (Postgres / DuckDB behind write_service), NOT in
    files -- read via the write_service /query endpoint, never a CSV. Pre-empts the
    data-source hallucination on attempt 1 (schema-PRM only catches it reactively).
    Table list derived from the live schema KL so it stays real. Cached per process;
    never raises."""
    global _DATA_ACCESS_CTX
    if _DATA_ACCESS_CTX is not None:
        return _DATA_ACCESS_CTX
    _DATA_ACCESS_CTX = (
        "DATA ACCESS: data lives in databases, never files (no CSV/JSON inputs; CSV is "
        "export-only). TWO PLANES -- (1) APP tables (mcp_server_registry, mcp_llm_axis_scores, "
        "mcp_score_disputes, orgs, users) are authoritative in the app Postgres: read via the "
        "app.db SQLAlchemy session -- import it VERBATIM: from app.db import get_session and "
        "from app.models import <Model>, use Depends(get_session). Do NOT define your own "
        "session/engine/models or an in-memory DB: a FastAPI module with no from app.db import "
        "is a HOLLOW build and is REJECTED by the no-hollow gate (mirror verdict_breakdown_api.py). "
        "Do not read app tables off :8772 (stale partial copy). (2) MESH/pipeline tables (mcp_signal_scores, mesh_memory) live in the "
        "ZoComputer store: read via write_service POST http://127.0.0.1:8772/query. "
        "SELF-TEST vs DATA LAYER: if a directive's acceptance mentions an 'in-memory store', "
        "that applies ONLY to the __main__ self-test -- override the dep via "
        "app.dependency_overrides[get_session] to a throwaway SQLite session for the test. "
        "The MODULE's OWN data access MUST remain from app.db import get_session + "
        "from app.models import <Model>; a module whose data layer is itself an in-memory/"
        "sqlite store (rather than just the test override) is HOLLOW and REJECTED. "
        "Keep output lean: minimal comments, no narration.")
    return _DATA_ACCESS_CTX


_RECIPE_ALLOW = {"webapp_backend_fastapi", "webapp_frontend_react", "webapp_fullstack", "module_from_exemplar", "architect"}
# Conservative inference: only the auth/RBAC/tenant SECURITY SPINE (where the generic
# single-file builder produced hollow stubs) is keyword-routed to the FastAPI recipe.
# Reports/search/etc. stay on architect.yaml unless the directive sets `recipe` explicitly.
_BACKEND_HINTS = ("oauth", "login_service", "rbac", "role_enforc", "require_role",
                  "tenant", "api_key", "session", "jwt", "auth_", "_auth", "org_model",
                  # app-surface: route every app API module to the FastAPI recipe (which
                  # ENFORCES reading mcp_llm_axis_scores via the DB session) -- without this
                  # they fell through to the generic builder and shipped hollow stubs.
                  "_api", "verdict", "scoring", "_consumer", "dashboard", "registry_search",
                  "report", "compliance", "risk_register", "risk_trend", "policy", "decisions", "watchlist")
_FRONTEND_HINTS = (".html", "_dashboard.", "frontend", "_ui.", "_view.", "react")


def _select_recipe(directive):
    """Pick the goose execute-recipe for a directive. Explicit `recipe` field wins
    (allowlisted); else infer the webapp backend/frontend recipe for clear app-spine
    modules; else None -> caller defaults to architect.yaml. Backward-compatible."""
    try:
        r = str(directive.get("recipe", "") or "").strip()
        if r in _RECIPE_ALLOW and r != "architect":
            return r
        blob = " ".join(str(directive.get(k, "")) for k in
                        ("directive_id", "id", "output_file", "target_file", "title", "description")).lower()
        try:
            _o = declared_output(directive)
            if _o:
                blob += " " + str(_o).lower()
        except Exception:
            pass
        if any(h in blob for h in _FRONTEND_HINTS):
            return "webapp_frontend_react"
        if any(h in blob for h in _BACKEND_HINTS):
            return "webapp_backend_fastapi"
    except Exception:
        pass
    return None


def run_goose_task(directive_id, content, extra_env=None, recipe=None):
    """Execute Goose on task file with timeout.

    extra_env (from build_routing.build_env_for) routes the architect
    (GOOSE_MODEL) + codegen (ZO_BUILD_TIER) up the ladder by complexity and
    carries task/phase for the build_artifact row builder_mcp emits."""
    log(f"Executing Goose for directive: {directive_id}")

    try:
        _recipe_name = recipe if recipe else "architect"
        recipe_path = PROJECT_DIR / "goose_recipes" / f"{_recipe_name}.yaml"
        if not recipe_path.exists():
            log(f"[recipe] {_recipe_name}.yaml missing -> architect.yaml")
            recipe_path = PROJECT_DIR / "goose_recipes" / "architect.yaml"
        if recipe:
            log(f"[recipe] {directive_id} -> {recipe_path.name}")
        task_desc = json.dumps(content)
        env = {**os.environ, **(extra_env or {})}
        if extra_env:
            log(f"[LADDER] {directive_id} -> {extra_env.get('GOOSE_MODEL')}")
        result = subprocess.run(
            ["goose", "run", "--recipe", str(recipe_path),
             "--params", f"task_description={task_desc}"],
            capture_output=True,
            text=True,
            timeout=GOOSE_TIMEOUT,
            cwd=str(PROJECT_DIR),
            env=env
        )
        
        if result.returncode == 0:
            log(f"Goose execution succeeded for {directive_id}")
            return {
                "success": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "directive_id": directive_id
            }
        else:
            log(f"Goose execution failed (exit {result.returncode}): {result.stderr}")
            return {
                "success": False,
                "error": result.stderr or "Unknown error",
                "directive_id": directive_id
            }
            
    except subprocess.TimeoutExpired:
        log(f"Goose timeout exceeded for {directive_id}")
        return {
            "success": False,
            "error": f"Timeout after {GOOSE_TIMEOUT}s",
            "directive_id": directive_id
        }
    except FileNotFoundError:
        log("Goose not installed - using fallback")
        return {
            "success": False,
            "error": "Goose not installed",
            "directive_id": directive_id
        }
    except Exception as e:
        log(f"Goose execution error: {e}")
        return {
            "success": False,
            "error": str(e),
            "directive_id": directive_id
        }

def _strip_code_fences(txt):
    """Strip markdown code fences the model may wrap a file in -- robust to
    ASYMMETRIC output. The old version only stripped when the response STARTED
    with a fence, so a code-first response with a stray trailing fence wrote the
    backticks into the .py and failed the Tier-0 py_compile gate (the 2026-06-23
    ghost_build cause). Now: prefer the FIRST well-formed fenced block; otherwise
    drop any standalone fence line so a leftover fence never reaches the gate."""
    import re
    s = txt.strip()
    _m = re.search(r"```[^\n]*\n(.*?)```", s, re.DOTALL)
    if _m:
        return _m.group(1).strip()
    _lines = [ln for ln in s.splitlines() if not ln.lstrip().startswith("```")]
    return chr(10).join(_lines).strip()


def call_minimax_fallback(directive):
    """Goose subprocess failed/unavailable -> route directive through the
    ladder shim (escalation.py, all 16 rungs). One HTTP call; the shim does
    MiniMax->Gemini->Gemma->Zo traversal internally."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    content = directive.get("content", directive.get("description", ""))
    log(f"Routing directive {directive_id} through ladder shim")
    try:
        resp = requests.post(SHIM_URL, json={
            "model": "zo-ladder-nvidia",  # capable tool/coder window (was zo-ladder-v1=MiniMax rung0)
            "messages": [
                {"role": "system", "content": "You are an autonomous builder for zo-sentinel. Output only the completed code/results."},
                {"role": "user", "content": f"Complete this task: {content}"},
            ],
            "temperature": 0.2,
            "max_tokens": 16384,
        }, timeout=600)
        if resp.status_code == 200:
            txt = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            # PERSIST the generated code to the declared output. goose-developer
            # ghosted (no file on disk); the fallback historically returned the code
            # as TEXT and discarded it -> output_confirmed() always False -> a
            # GUARANTEED ghost for any directive goose itself couldn't build. Write it
            # so the EXISTING output_confirmed + Tier-0 py_compile gate in the main loop
            # can validate -- non-compiling/garbage output still re-ghosts (it is never
            # auto-completed). declared_output() is None for edit-class tasks
            # (wire_/rewire_/...), so those are skipped and keep trusting process success.
            try:
                _out = declared_output(directive)
                _code = _strip_code_fences(txt) if txt else ""
                if _out is not None and _code.strip():
                    _out.parent.mkdir(parents=True, exist_ok=True)
                    _out.write_text(_code, encoding="utf-8")
                    log(f"[fallback] wrote {len(_code)} bytes -> {_out.name} (Tier-0 gate will validate)")
            except Exception as _e:
                log(f"[fallback] write failed: {type(_e).__name__}: {_e}")
            return {"success": bool(txt), "result": txt, "fallback": "ladder_shim", "directive_id": directive_id}
        return {"success": False, "error": f"shim {resp.status_code}: {resp.text[:200]}", "directive_id": directive_id}
    except Exception as e:
        return {"success": False, "error": f"shim call failed: {type(e).__name__}: {e}", "directive_id": directive_id}

def route_to_coworker(directive):
    """Write high-complexity directive to coworker handoff queue.
    Syncthing syncs to tower; Claude Desktop / Coworker picks it up.
    """
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    handoff_dir = Path("/home/workspace/shared/outputs/coworker_queue")
    handoff_dir.mkdir(parents=True, exist_ok=True)

    handoff = {
        "directive_id": directive_id,
        "complexity": directive.get("complexity", "high"),
        "description": directive.get("description", ""),
        "content": directive.get("content", ""),
        "source": directive.get("source", ""),
        "handoff_at": get_utc_now(),
        "status": "pending_coworker",
        "instructions": (
            "High-complexity directive requiring Claude Code via Coworker.\n"
            "Codebase root: /home/workspace/zo_sentinel/\n"
            "Write outputs to: /home/workspace/zo_sentinel/ (in-place)\n"
            "Log results to: /home/workspace/logs/{directive_id}_coworker.log\n"
            "Mark done by writing: /home/workspace/zo_sentinel/directives/done/{directive_id}.json"
        ).format(directive_id=directive_id)
    }

    out = handoff_dir / f"{directive_id}.json"
    out.write_text(__import__("json").dumps(handoff, indent=2))
    log(f"[COWORKER] Handoff written: {out}")
    return out



# =============================================================================
# RESULT HANDLING
# =============================================================================

def write_result(directive_id, success, result_text="", error="", fallback_used=False):
    """Write execution result to shared outputs."""
    SHARED_OUTPUTS.mkdir(parents=True, exist_ok=True)
    output_file = SHARED_OUTPUTS / f"{directive_id}.result"
    
    result_data = {
        "directive_id": directive_id,
        "success": success,
        "timestamp": get_utc_now(),
        "result": result_text,
        "error": error,
        "fallback_used": fallback_used,
        "source": "goose_tier1" if not fallback_used else "minimax_fallback"
    }
    
    output_file.write_text(json.dumps(result_data, indent=2))
    log(f"Result written to {output_file}")
    return output_file

def mark_directive_completed(directive):
    """Mark directive as completed in mesh_events table."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    
    try:
        # Update mesh_events table
        ws_write("mesh_events", {
            "agent_id": "goose_tier1",
            "event_type": "DIRECTIVE_COMPLETE",
            "payload": json.dumps({"directive_id": directive_id, "status": "completed",
                                   "completed_at": get_utc_now()}),
            "severity": "INFO",
            "created_at": get_utc_now()
        })
        Path(f"/home/workspace/zo_sentinel/directives/{directive_id}.done.json").write_text(
            json.dumps({"directive_id": directive_id, "completed_at": get_utc_now()}))
        log(f"Marked directive {directive_id} completed (mesh_events + done sentinel)")
    except Exception as e:
        log(f"Failed to mark directive completed: {e}")
    
    # Move file from pending to done if exists
    pending_file = PENDING_DIR / f"{directive_id}.json"
    if pending_file.exists():
        done_file = DONE_DIR / f"{directive_id}.json"
        try:
            content = pending_file.read_text()
            data = json.loads(content)
            data["status"] = "completed"
            data["completed_at"] = get_utc_now()
            data["completed_by"] = "goose_tier1"
            done_file.write_text(json.dumps(data, indent=2))
            pending_file.unlink()
            log(f"Moved {directive_id} to done directory")
        except Exception as e:
            log(f"Failed to move directive file: {e}")

def _emit_build_artifact_for(directive):
    """Emit the build_artifact mesh row for a confirmed creation build.

    RESTORES the emission builder_mcp.delegate_to_builder used to guarantee. #73
    moved goose to the developer extension (it writes files itself) and made the
    artifact depend on the model calling register_build -- which it does NOT do
    reliably, so confirmed builds landed on disk + .done but produced NO
    build_artifact -> the ingestor/governor/publisher were blind -> no PR. We know
    the file + tier here, so emit it directly, decoupled from the model.

    Edit-class directives (declared_output None -- wire/rewire/...) create no
    single new file, so no artifact (their multi-file diffs need a separate
    publisher path)."""
    out = declared_output(directive)
    if out is None or not out.is_file():
        return
    try:
        size = out.stat().st_size
        rel = (str(out.relative_to(PROJECT_DIR))
               if str(out).startswith(str(PROJECT_DIR)) else out.name)
        row = build_artifact_row(
            file=rel,
            content_bytes=size,
            context_type=str(directive.get("interface")
                             or directive.get("context_type") or "utility"),
            tier=tier_for_complexity(directive.get("complexity")),
            model=os.environ.get("GOOSE_MODEL", ""),
            backend="goose_developer",
            phase=str(directive.get("phase", "")),
            task=resolve_directive_id(directive),
        )
        row["created_at"] = get_utc_now()
        resp = ws_write("mesh_memory", row)
        if not (isinstance(resp, dict) and resp.get("ok")):
            # ws_write swallows errors and returns {"ok": False}; do NOT let a
            # failed write masquerade as "Emitted" (the silent-drop we chased:
            # build on disk + .done, but no build_artifact -> publisher blind).
            log(f"[artifact] ws_write returned {resp} for {rel} -- retrying once")
            resp = ws_write("mesh_memory", row)
        if isinstance(resp, dict) and resp.get("ok"):
            log(f"Emitted build_artifact for {rel} ({size} bytes) resp={resp}")
        else:
            log(f"[artifact] FAILED to persist build_artifact for {rel}: resp={resp} "
                f"-- publisher will NOT see this build")
    except Exception as e:
        log(f"Failed to emit build_artifact for "
            f"{directive.get('directive_id') or directive.get('id')}: {e}")


def _record_build_provenance(directive, success, smoke_result, attempt,
                             rescue_count, routed_model="", error=""):
    """Write one build_provenance row per ATTEMPT -- the failure-matrix substrate
    (Phase 4). build_provenance was defined-but-unwired, so the goose path recorded
    no per-attempt rung+outcome. Best-effort: a write_service hiccup must never fail
    the build (mirrors _emit_build_artifact_for). `routed_model` is the alias this
    build ACTUALLY routed to (build_env_for's GOOSE_MODEL) -- the rung the matrix
    learns on; we record THAT, not the daemon's ambient GOOSE_MODEL env (which is a
    single global and would make every row look identical). attempt drives the
    deterministic build_id."""
    try:
        out = declared_output(directive)
        output_path, output_bytes = "", 0
        if out is not None and out.is_file():
            output_path = (str(out.relative_to(PROJECT_DIR))
                           if str(out).startswith(str(PROJECT_DIR)) else out.name)
            output_bytes = out.stat().st_size
        row = build_provenance_row(
            directive_id=resolve_directive_id(directive),
            directive_type=str(directive.get("interface")
                               or directive.get("context_type") or "utility"),
            complexity=str(directive.get("complexity") or "unknown"),
            model=(routed_model or os.environ.get("GOOSE_MODEL", "")
                   or tier_for_complexity(directive.get("complexity"))),
            success=bool(success),
            smoke_result=smoke_result,
            attempt=attempt,
            rescue_count=rescue_count,
            output_path=output_path,
            output_bytes=output_bytes,
            error=str(error or ""),
        )
        resp = ws_write("build_provenance", row)
        if not (isinstance(resp, dict) and resp.get("ok")):
            log(f"[provenance] ws_write returned {resp} for {row['build_id']}")
    except Exception as e:
        log(f"[provenance] failed for "
            f"{directive.get('directive_id') or directive.get('id')}: {e}")


# Git commit of the state files is OFF by default: the host zo_sentinel clone is
# `git reset --hard origin/main` on every refresh (2026-05-31 ops note), so a
# local checkpoint commit there is futile/conflicting. The on-disk manifest is
# the resume source (it survives reset --hard because the state files are
# gitignored, and survives Modal reboot because the disk persists). Opt in with
# ZO_STATE_GIT_COMMIT=1 only off-host.
ZO_STATE_GIT_COMMIT = os.environ.get("ZO_STATE_GIT_COMMIT", "") not in ("", "0", "false", "False")


def _schema_prm_gate(directive, directive_id):
    """GraphifyKL pre-build PRM: deterministically reject a build whose declared output
    hallucinates the schema (unknown model columns/attrs, or inline mock models) vs the
    real app.models. On a violation record a lesson so the NEXT attempt is corrected
    (closed-loop feedback), then block completion. Best-effort: any internal error -> pass
    (never block a good build on a tooling hiccup)."""
    try:
        out = declared_output(directive)
        if out is None or not out.exists():
            return True
        # Schema PRM lints PYTHON modules against app.models. A non-.py output
        # (.html front-end view, .sql, ...) has no Python schema surface, and the
        # AST linter reports its non-Python syntax as a bogus "violation" that
        # ghosts the build (e.g. a dispute admin_disputes.html). Only gate .py.
        if out.suffix.lower() != ".py":
            return True
        import schema_kl
        try:
            kl = schema_kl.build_schema_kl()
        except Exception:
            try:
                kl = schema_kl.load_schema_kl(str(PROJECT_DIR / "graphify-out" / "schema_kl.json"))
            except Exception:
                return True
        violations = schema_kl.lint_source(out.read_text(encoding="utf-8"), kl)
        if not violations:
            return True
        obs = ("schema PRM rejected this build -- the module uses a schema not in app.models. "
               "Fix EXACTLY these before rebuilding: " + " | ".join(violations[:8]))
        log(f"[schema-prm] {directive_id}: BLOCKED -- {len(violations)} schema violation(s):")
        for _v in violations[:8]:
            log(f"    - {_v}")
        try:
            record_lesson(LESSONS_DIR, out.name, directive_id, "schema_prm", obs, severity=3)
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"[schema-prm] {directive_id}: gate error (passing): {e}")
        return True


def _no_hollow_gate(directive, directive_id):
    """Reject a HOLLOW build BEFORE it is marked .done -- the earliest seam.

    A hollow scaffold (standalone FastAPI module with no app.db/app.models layer,
    or a mock/placeholder DB) compiles cleanly, so the Tier-0 syntax gate passes it
    and the build completes. The publisher (#1450) and CI then reject it -- correctly,
    but far too late: the build tokens are already spent, and the .done sentinel it
    left behind silently swallows any same-name reseed (which is why a rejected
    build has to come back renamed *_v2). Blocking HERE means the directive never
    completes, so it stays live and RETRIES -- and the lesson below puts the exact
    rejection into the next attempt's context, closing the loop the way the schema
    PRM does. Prose told the builder about this rule and it leaked anyway; the rule
    now lives in code (zo_sentinel.gates.hollow), shared verbatim with CI.

    Fail-open on any internal error, and live-flippable via the policy layer
    (builder.no_hollow_gate) -- a false positive here starves the builder, so the
    chairman must be able to switch it off without a redeploy. Publisher + CI remain
    as backstops either way.
    """
    try:
        try:
            from zo_sentinel import policy as _policy
            if not _policy.flag("builder.no_hollow_gate", directives_root=DIRECTIVES_PATH):
                return True
        except Exception:
            pass   # policy fault -> gate stays ON (fail-safe, not fail-open)
        out = declared_output(directive)
        if out is None or not out.exists():
            return True
        try:
            rel = str(out.relative_to(PROJECT_DIR)).replace("\\", "/")
        except Exception:
            rel = out.name
        reason = hollow_scaffold_scan(rel, out.read_text(encoding="utf-8"))
        if reason is None:
            return True
        obs = ("no-hollow gate rejected this build -- " + reason + ". The module must read the "
               "REAL data layer: import from app.db / app.models (get_session), mirror "
               "verdict_breakdown_api.py, and carry NO mock/placeholder/in-memory DB. "
               "Rebuild it against the real schema.")
        log(f"[no-hollow] {directive_id}: BLOCKED -- {reason} ({out.name})")
        try:
            record_lesson(LESSONS_DIR, out.name, directive_id, "no_hollow", obs, severity=3)
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"[no-hollow] {directive_id}: gate error (passing): {e}")
        return True


def _selftest_gate(directive, directive_id):
    """Run the module's __main__ self-test and require PASS before completing. Degrades to
    True (Tier-0 only) when there is no self-test or it fails purely on an environment/import
    error (so we never false-fail on missing deps), but a self-test that RUNS and does not
    print PASS is a real failure that BLOCKS completion. Closes the hole where the ladder-shim
    fallback stamped .done on a parse-only gate (e.g. server_compare_api hallucinating a
    'published_overall_risk' axis would now be caught instead of shipped)."""
    import subprocess, sys as _sys
    try:
        out = declared_output(directive)
    except Exception:
        return True
    if out is None or not out.exists():
        return True
    try:
        src = out.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return True
    if "__main__" not in src:
        return True
    try:
        import os as _os
        # in-memory SQLite + no Clerk -> the self-test imports app.db/app.models and runs
        # WITHOUT Postgres, DuckDB or Docker (lightweight, short-lived, one at a time).
        _env = {**_os.environ, "DATABASE_URL": "sqlite://", "CLERK_PUBLISHABLE_KEY": ""}
        proc = subprocess.run([_sys.executable, str(out)], capture_output=True,
                              text=True, timeout=120, cwd=str(PROJECT_DIR), env=_env)
    except Exception as e:
        log(f"[selftest] {directive_id}: could not run ({type(e).__name__}: {e}) -- Tier-0 only")
        return True
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode == 0 and "PASS" in (proc.stdout or ""):
        log(f"[selftest] {directive_id}: self-test PASS")
        return True
    if proc.returncode != 0 and ("ModuleNotFoundError" in combined or "ImportError" in combined):
        log(f"[selftest] {directive_id}: import/env failure -- degrading to Tier-0 (not blocking)")
        return True
    log(f"[selftest] {directive_id}: self-test FAILED -- blocking completion :: " + combined.strip()[-400:])
    return False


def _syntax_gate(directive, directive_id):
    """Tier-0 syntax gate on the declared output, recorded to the Default-FAIL
    manifest. Returns True when the file parses, or when there is no single
    declared .py output to gate (edit-class directives -- output_confirmed
    already validated those). Tier-1 import is deliberately NOT a hard gate:
    host-only deps (mcp/httpx/abs paths) false-FAIL it (workflow caveat), so it
    would block valid builds -- it is recorded as advisory in a later phase, not
    a completion blocker here. Never raises: a gate error must not regress a
    build that output_confirmed already proved landed on disk."""
    if run_gates is None:
        return True
    out = declared_output(directive)
    if out is None or not out.is_file():
        return True
    # Front-end outputs (.html) get a LENIENT structural gate, not py_compile:
    # parse with the stdlib HTML parser and require non-empty markup (the FE
    # analogue of the Tier-0 syntax gate / mirrors the CI front-end lane FE0).
    # Good HTML completes; empty/garbage HTML re-ghosts. Never false-fails: any
    # parser hiccup -> allow (output_confirmed already proved the file landed).
    if out.suffix.lower() in (".html", ".htm"):
        try:
            from html.parser import HTMLParser
            class _HG(HTMLParser):
                def __init__(self):
                    super().__init__(convert_charrefs=True)
                    self.tags = 0
                    self.body = False
                def handle_starttag(self, tag, attrs):
                    self.tags += 1
                def handle_data(self, data):
                    if data.strip():
                        self.body = True
            hg = _HG()
            hg.feed(out.read_text(encoding="utf-8", errors="replace"))
            ok = hg.tags > 0 and hg.body
            if not ok:
                log(f"[eval-gate] {directive_id}: HTML gate FAIL on {out.name} "
                    f"(empty / no renderable markup) -- not completing")
            return ok
        except Exception as e:
            log(f"[eval-gate] {directive_id}: HTML gate error ({e}) -- allowing")
            return True
    if out.suffix != ".py":
        return True
    try:
        report = run_gates(out, [0])           # syntax only -- cheap, no false-fails
        sl.record_gate_result(directive_id, report)
        if not report.get("passed"):
            log(f"[eval-gate] {directive_id}: Tier-0 syntax FAIL on {out.name} -- not completing")
        return bool(report.get("passed"))
    except Exception as e:
        log(f"[eval-gate] {directive_id}: gate error ({e}) -- allowing (output_confirmed held)")
        return True


_BANDIT_READY = None  # tri-state cache: None=unchecked, True=available, False=unavailable


def _ensure_bandit():
    """One-time best-effort: make `python -m bandit` available (lazy pip install,
    cached so the build hot path pays the check once). Never raises."""
    global _BANDIT_READY
    if _BANDIT_READY is not None:
        return _BANDIT_READY
    import importlib.util
    if importlib.util.find_spec("bandit") is not None:
        _BANDIT_READY = True
        return True
    try:
        import subprocess, sys as _sys
        subprocess.run([_sys.executable, "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "bandit"],
                       capture_output=True, text=True, timeout=180)
        _BANDIT_READY = importlib.util.find_spec("bandit") is not None
    except Exception:
        _BANDIT_READY = False
    if not _BANDIT_READY:
        log("[security] bandit unavailable -- advisories disabled (functional builds unaffected)")
    return _BANDIT_READY


def _security_advisory(directive, directive_id):
    """SECONDARY (non-blocking) signal. AFTER a build has passed the hard gates and
    SHIPPED, run Bandit on the declared .py output and, on MEDIUM+ findings, record a
    LOW-severity security lesson under a SEPARATE subject ('<target>::security') so it
    teaches the NEXT build WITHOUT clobbering the functional/schema lesson and WITHOUT
    ever blocking a working, schema-bound build. Security is the 2nd objective:
    functionality + schema-binding + novelty come first. Best-effort; never raises."""
    try:
        out = declared_output(directive)
        if out is None or out.suffix.lower() != ".py" or not out.is_file():
            return
        if not _ensure_bandit():
            return
        import subprocess, sys as _sys, json as _json
        proc = subprocess.run(
            [_sys.executable, "-m", "bandit", "-q", "-f", "json", "-ll", str(out)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_DIR))
        try:
            data = _json.loads(proc.stdout or "{}")
        except Exception:
            return
        results = data.get("results", [])
        sec_subject = f"{out.name}::security"
        if not results:
            try:
                resolve_lessons(LESSONS_DIR, sec_subject)  # clean now -> close prior advisory
            except Exception:
                pass
            return
        tops = []
        for r in results[:4]:
            tops.append(f"{r.get('test_id','')} {r.get('issue_severity','')}: "
                        f"{str(r.get('issue_text',''))[:110]} (line {r.get('line_number','?')})")
        obs = ("Bandit flagged " + str(len(results)) + " MEDIUM+ security issue(s); when it does "
               "NOT compromise the working feature, prefer the safe form: " + " | ".join(tops))
        record_lesson(LESSONS_DIR, sec_subject, directive_id, "security_advisory", obs, severity=1)
        log(f"[security] {directive_id}: {len(results)} Bandit MEDIUM+ finding(s) -> advisory lesson (non-blocking)")
    except Exception as e:
        log(f"[security] {directive_id}: advisory skipped ({type(e).__name__}: {e})")


def _complete(directive, directive_id, result_text, fallback_used=False,
              routed_model=""):
    """A directive's declared output IS on disk AND passed the Tier-0 gate ->
    record + mark done for real."""
    write_result(directive_id, True, result_text, fallback_used=fallback_used)
    _emit_build_artifact_for(directive)   # restore publisher feed (#73 dropped this)
    mark_directive_completed(directive)
    # Closed-loop: a green build on this target auto-resolves any open lesson.
    try:
        _rout = declared_output(directive)
        resolve_lessons(LESSONS_DIR, _rout.name if _rout is not None else directive_id)
    except Exception:
        pass

    # SECONDARY objective (non-blocking): record a Bandit security advisory for the
    # NEXT build of this target. The feature already passed schema + self-test and
    # SHIPPED -- security teaches the next build, it never blocks functional work.
    try:
        _security_advisory(directive, directive_id)
    except Exception:
        pass
    # Provenance BEFORE clear_ghost so rescue_count reflects the retries it took.
    prior_ghosts = ghost_attempts(DIRECTIVES_PATH, directive_id)
    _record_build_provenance(directive, success=True, smoke_result="pass",
                             attempt=prior_ghosts + 1, rescue_count=prior_ghosts,
                             routed_model=routed_model)
    clear_ghost(DIRECTIVES_PATH, directive_id)
    # Default-FAIL contract: record the proven PASS + checkpoint for crash-resume.
    # Idempotent (record_pass overwrites; checkpoint refreshes the cursor).
    try:
        sl.record_pass(directive_id, f"output_confirmed + Tier-0 gate: {declared_output(directive)}")
        sl.checkpoint(directive_id, "complete")
        if ZO_STATE_GIT_COMMIT:
            sl.commit_checkpoint(f"chore(state): {directive_id} complete")
    except Exception as e:
        log(f"[loopback] checkpoint failed for {directive_id}: {e}")

def _mark_directive_failed(directive, directive_id, reason):
    """Give up after repeated ghost builds: write a .failed sentinel (so the
    runner stops churning and the generator stops re-proposing) + surface it.
    NOT a .done -- it never built; this makes the failure visible instead of
    masquerading as success the way ghost-completion did."""
    log(f"[ghost-guard] {directive_id}: GIVING UP after {MAX_GHOST_ATTEMPTS} ghost builds -- {reason}")
    # Closed-loop lesson: record WHY this gave up, keyed by the target it failed
    # on, so the architect read-gate (separate PR) can avoid re-proposing known-bad
    # work. File-based (zero DB load) + a best-effort mesh_memory mirror for history.
    # Wrapped: a lesson write must NEVER regress the failure path.
    try:
        _lout = declared_output(directive)
        _lsub = _lout.name if _lout is not None else directive_id
        _lstem = _lout.stem if _lout is not None else ""
        _lparts = [x for x in _lstem.split("_") if x]
        _lttype = ("doubled_path" if len(_lparts) >= 2 and _lparts[0] == _lparts[1]
                   else "ghost_no_output")
        record_lesson(LESSONS_DIR, _lsub, directive_id, _lttype, reason, severity=3)
        ws_write("mesh_memory", {
            "agent_id": "goose_tier1",
            "memory_type": "lesson_learned",
            "content": json.dumps({"subject_ref": _lsub, "directive_id": directive_id,
                                   "task_type": _lttype, "observation": reason,
                                   "severity": 3, "status": "open"}),
            "importance": 0.5,
            "created_at": get_utc_now(),
        })
    except Exception as _le:
        log(f"[lesson] emit failed for {directive_id}: {_le}")
    _failed_payload = json.dumps({"directive_id": directive_id, "reason": reason,
                                  "failed_at": get_utc_now()})
    try:
        Path(f"{DIRECTIVES_PATH}/{directive_id}.failed.json").write_text(_failed_payload)
    except Exception as e:
        log(f"Failed to write .failed sentinel for {directive_id}: {e}")
    # Durable copy outside the git tree -> survives `git clean` on respawn.
    try:
        DURABLE_QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        (DURABLE_QUARANTINE_DIR / f"{directive_id}.failed.json").write_text(_failed_payload)
    except Exception as e:
        log(f"durable quarantine write failed for {directive_id}: {e}")
    try:
        ws_write("mesh_events", {
            "agent_id": "goose_tier1",
            "event_type": "DIRECTIVE_GHOST_FAILED",
            "payload": json.dumps({"directive_id": directive_id, "reason": reason}),
            "severity": "WARNING",
            "created_at": get_utc_now(),
        })
    except Exception:
        pass
    clear_ghost(DIRECTIVES_PATH, directive_id)
    pending_file = PENDING_DIR / f"{directive_id}.json"
    if pending_file.exists():
        try:
            pending_file.unlink()
        except Exception:
            pass

def _ghost_or_fail(directive, directive_id, routed_model=""):
    """goose/fallback reported success but the declared output never appeared.
    Count the ghost attempt; requeue for another try, or fail it once the cap
    is hit. Crucially we do NOT mark it .done -- that was the regression."""
    n = bump_ghost(DIRECTIVES_PATH, directive_id, get_utc_now())
    try:
        sl.record_fail(directive_id, f"ghost/gate fail, attempt {n}")  # Default-FAIL history
    except Exception:
        pass
    _record_build_provenance(directive, success=False, smoke_result="ghost",
                             attempt=n, rescue_count=n, routed_model=routed_model,
                             error="ghost build: declared output_file was not produced")
    if n >= MAX_GHOST_ATTEMPTS:
        _mark_directive_failed(directive, directive_id,
                               "declared output never produced (ghost build)")
        return
    write_result(directive_id, False,
                 error="ghost build: declared output_file was not produced")
    try:
        ws_write("mesh_events", {
            # mesh_events.agent_id is NOT NULL, and content/complexity/source/
            # retry_count are not columns -> the old row 500'd (constraint) + got
            # its keys dropped. Use the real schema (agent_id + payload).
            "agent_id": "goose_tier1",
            "event_type": "DIRECTIVE_GHOST_RETRY",
            "payload": json.dumps({"directive_id": directive_id, "retry_count": n}),
            "severity": "WARNING",
            "created_at": get_utc_now(),
        })
        log(f"[ghost-guard] {directive_id}: ghost attempt {n}/{MAX_GHOST_ATTEMPTS}, requeued for retry")
    except Exception as e:
        log(f"Failed to requeue {directive_id}: {e}")

def log_directive_routed(directive_id, source, complexity, method):
    """Log directive routing decision."""
    log(f"[ROUTE] directive_id={directive_id} complexity={complexity} source={source} method={method}")
    
    try:
        ws_write("audit_log", {
            "event_type": "goose_route",
            "actor": "goose_runner",
            "target_server_id": directive_id,
            "detail": json.dumps({
                "complexity": complexity,
                "source": source,
                "method": method
            }),
            "created_at": get_utc_now()
        })
    except Exception as e:
        log(f"Failed to log routing: {e}")

# =============================================================================
# MAIN LOOP
# =============================================================================

def run():

    # Route Goose through the ladder shim (escalation.py, 16 rungs).
    # Shim holds no creds; escalation.py reads vault-hydrated keys from env.
    import os
    os.environ['GOOSE_PROVIDER']  = 'openai'
    os.environ['GOOSE_MODEL']     = 'MiniMax-Text-01'
    os.environ['OPENAI_BASE_URL'] = 'http://127.0.0.1:8796/v1'
    os.environ['OPENAI_API_KEY']  = 'dummy_key_for_shim'  # bypasses keyring check
    """Main daemon loop."""
    log(f"=== Goose Runner starting ===")
    log(f"Service: {SERVICE_NAME}, Port: {SERVICE_PORT}")
    
    ensure_directories()
    check_single_instance()

    # Default-FAIL loop-back: surface the last checkpoint so a crash-restarted
    # container shows where it left off (file-based; survives reboot + refresh).
    try:
        cursor = sl.resume()
        if cursor:
            log(f"[loopback] resume cursor: last_step={cursor.get('last_step')} at={cursor.get('at')}")
    except Exception as e:
        log(f"[loopback] resume read failed: {e}")

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start heartbeat thread
    heartbeat_thread = Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    log("Heartbeat thread started")
    
    # Check Goose installation
    goose_installed = check_goose_installed()
    if goose_installed:
        log("Goose CLI verified")
    else:
        log("Goose CLI not available - will use fallback on first directive")
    
    cycle_count = 0
    
    while True:
        try:
            cycle_count += 1
            send_heartbeat()
            log(f"=== Cycle {cycle_count} ===")
            
            # Load eligible directives
            directives = load_directives_from_mesh()
            
            if not directives:
                log("No eligible directives found")
            else:
                log(f"Processing {len(directives)} directives")
            
            for directive in directives:
                directive_id = directive.get("directive_id") or directive.get("id", "unknown")
                complexity = directive.get("complexity", "unknown")
                source = directive.get("source", "unknown")
                
                if not is_goose_eligible(directive):
                    log(f"Skipping non-eligible directive: {directive_id} (complexity={complexity})")
                    continue

                # High complexity -> coworker handoff, not Goose
                if complexity == "high":
                    log(f"[COWORKER] Routing high-complexity directive: {directive_id}")
                    route_to_coworker(directive)
                    mark_directive_completed(directive)
                    continue
                
                log(f"Processing directive: {directive_id} (complexity={complexity})")
                log_directive_routed(directive_id, source, complexity, "goose_tier1")
                
                # Write task spec
                write_task_spec(directive)
                sl.init_manifest([directive_id])   # Default-FAIL until proven (idempotent: setdefault)

                # Build via Goose, falling back to MiniMax. CRITICAL: a directive
                # is "done" ONLY when its declared output_file actually lands on
                # disk (output_confirmed) AND the file parses (Tier-0 gate). Both
                # goose and the fallback can report success WITHOUT producing the
                # file, or produce a syntactically broken one -- stamping that
                # .done was the ghost-completion regression that burned directives.
                # Phase 5: route by the prior ghost-retry count so a re-asserted
                # directive escalates up the ladder (no-op unless ZO_ESCALATE set).
                # Capture the routed alias here so build_provenance records the rung
                # the build ACTUALLY used (not the daemon's ambient GOOSE_MODEL).
                _attempt = ghost_attempts(DIRECTIVES_PATH, directive_id)
                # route by the recipe we will actually run (inferred recipe was not stamped -> app builds fell to MiniMax)
                directive["recipe"] = _select_recipe(directive) or directive.get("recipe", "")
                _routed_env = build_env_for(directive, attempt=_attempt,
                                            matrix_rows=failure_matrix_cached())
                _routed_model = _routed_env.get("GOOSE_MODEL", "")
                produced = False
                # Compose the fully-grounded task ONCE, for BOTH engines. The
                # engine-fallback fix (2026-07-02): the deterministic engine used
                # to get only the raw content while goose got graph + lessons +
                # data-access grounding -- so "both engines failed" ghosts were
                # often the second engine failing for lack of the context the
                # first already had.
                _task = directive.get("content", "")
                _gctx = _graph_context(directive)   # Phase 3: fold graph structure into the task
                if _gctx:
                    _task = f"{_task}\n\n{_gctx}"
                _lctx = _lessons_context(directive)   # closed-loop READER: prior failures on this target
                if _lctx:
                    _task = f"{_task}\n\n{_lctx}"
                _dctx = _data_access_context(directive)   # proactive DB-access grounding (pre-empt CSV hallucination)
                if _dctx:
                    _task = f"{_task}\n\n{_dctx}"
                if goose_installed:
                    result = run_goose_task(directive_id, _task, _routed_env, recipe=_select_recipe(directive))
                    if (result.get("success") and output_confirmed(directive)
                            and _syntax_gate(directive, directive_id)
                            and _schema_prm_gate(directive, directive_id)
                            and _no_hollow_gate(directive, directive_id)
                            and _selftest_gate(directive, directive_id)):
                        _complete(directive, directive_id, result.get("stdout", ""),
                                  routed_model=_routed_model)
                        produced = True
                    elif result.get("success"):
                        log(f"[ghost-guard] {directive_id}: goose reported success but output "
                            f"missing or failed Tier-0 gate ({declared_output(directive)}) "
                            f"-- not completing")
                    else:
                        log(f"Goose failed for {directive_id}: {result.get('error')}")

                if not produced:
                    # Engine fallback, first-class (gated, fail-open): the
                    # grounded deterministic engine with per-attempt capable-rung
                    # escalation and ONE bounded repair round. Falls back to the
                    # legacy bare-prompt call_minimax_fallback when the gate is
                    # off or the module errors -- behavior is IDENTICAL to before
                    # unless directives/.engine_build_on is "1". Completion
                    # authority stays with the gate chain below either way.
                    fallback_result = None
                    try:
                        from zo_sentinel import engine_build as _engine
                        if _engine.enabled(DIRECTIVES_PATH):
                            fallback_result = _engine.build_with_engine(
                                directive, _task, attempt=_attempt, log=log)
                            if fallback_result.get("model"):
                                _routed_model = f"engine:{fallback_result['model']}"
                    except Exception as _ee:
                        log(f"[engine] unavailable ({type(_ee).__name__}: {_ee}) "
                            f"-- fail-open to legacy fallback")
                        fallback_result = None
                    if fallback_result is None:
                        fallback_result = call_minimax_fallback(directive)
                    if (fallback_result.get("success") and output_confirmed(directive)
                            and _syntax_gate(directive, directive_id)
                            and _schema_prm_gate(directive, directive_id)
                            and _no_hollow_gate(directive, directive_id)
                            and _selftest_gate(directive, directive_id)):
                        _complete(directive, directive_id,
                                  fallback_result.get("result", ""), fallback_used=True,
                                  routed_model=_routed_model)
                        produced = True

                if not produced:
                    # Neither path produced the declared output -> ghost build.
                    # Retry (bounded) instead of stamping a bogus .done.
                    _ghost_or_fail(directive, directive_id, routed_model=_routed_model)

                # Small delay between directives
                time.sleep(2)
            
            log(f"Cycle {cycle_count} complete, sleeping {POLL_SECS}s")
            time.sleep(POLL_SECS)
            
        except Exception as e:
            log(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(POLL_SECS)

# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    run()