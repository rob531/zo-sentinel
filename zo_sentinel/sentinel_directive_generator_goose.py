#!/usr/bin/env python3
"""
sentinel_directive_generator_goose.py -- Goose-driven directive generator.

PHASE 0b SIBLING DAEMON. Runs ALONGSIDE the existing
sentinel_directive_generator.py without touching it. Different SERVICE_NAME,
different heartbeat key, different output directory.

KEY DESIGN DECISIONS (idempotency / non-breakage):
  1. Writes proposals to directives/proposed/, NEVER to directives/pending/.
     goose_runner.py does not watch proposed/, so this daemon CANNOT
     accidentally feed the active build chain. Promotion is a separate step.
  2. The existing sentinel_directive_generator.py keeps running unchanged.
     If this daemon crashes, the legacy generator still cycles (currently
     producing 0 directives/cycle, but the existing behavior is preserved).
  3. NOT registered in /etc/zo/supervisord-user.conf by this code. Robin
     adds the supervisord block manually when ready. See ROLLOUT.md.
  4. All LLM calls flow through the existing ladder_shim:8796 + Goose
     subprocess pattern. No new model. No new API key. No new shim.
  5. Read-only access to write_service /query for context bundle. Heartbeat
     is best-effort and never raises.

EQUIVALENCE TO EXISTING GENERATOR:
  - Same cycle structure (poll, build context, generate, sleep)
  - Same heartbeat cadence (60s)
  - Same prompt INPUTS (schema, failed_modules, recent_failures,
    registry_summary, layer1)
  - DIFFERENT prompt OUTPUT path: subprocess(goose run --recipe
    directive_architect.yaml) instead of call_minimax(prompt) + JSON-parse
  - DIFFERENT write target: directives/proposed/ instead of directives/pending/
"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVICE_NAME    = "directive_generator_goose"   # distinct from legacy
SENTINEL_DIR    = Path("/home/workspace/zo_sentinel")
RECIPE_PATH     = SENTINEL_DIR / "goose_recipes" / "directive_architect.yaml"
PROPOSED_DIR    = SENTINEL_DIR / "directives" / "proposed"
LOG_PATH        = Path("/home/workspace/logs/directive_generator_goose.log")

POLL_SECS       = int(os.environ.get("DGG_POLL_SECS", 600))    # 10 min default
HEARTBEAT_SECS  = int(os.environ.get("DGG_HEARTBEAT_SECS", 60))
GOOSE_TIMEOUT   = int(os.environ.get("DGG_GOOSE_TIMEOUT", 480))
MAX_PROPOSED    = int(os.environ.get("DGG_MAX_PROPOSED_DEPTH", 40))
IDLE_GATE       = os.environ.get("DGG_IDLE_GATE", "1") == "1"   # batch-when-idle (herd-safe)
IDLE_MIN        = int(os.environ.get("DGG_IDLE_MIN", 8))        # build side quiet >= N min = idle
LAYER1_FIELD_CAP = int(os.environ.get("DGG_LAYER1_FIELD_CAP", 4000))  # per-field char cap on the
                            # layer1 knowledge maps. The FULL maps made the base ctx ~38KB, which at
                            # 52KB total (with the module list) made the slow ladder architect TIME
                            # OUT on ~half its 480s cycles. Capping each of the 4 fields to ~4KB
                            # keeps the highest-signal head, shrinks base ctx to ~16-20KB.
CTX_MODULE_BUDGET = 30000   # byte ceiling for the WHOLE ctx incl. the module list, under the 60000
                            # json.dumps cap. With layer1 capped, base ctx ~20KB; 30000 leaves ~10KB
                            # for ~300 already_built_modules. Total ctx ~30KB -> architect completes
                            # reliably (the prior 38KB cycle finished rc=0 in ~213s; 52KB was flaky).
HEARTBEAT_URL   = "http://127.0.0.1:8772/write"
WS_QUERY_URL    = "http://127.0.0.1:8772/query"

PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [directive_gen_goose] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(SERVICE_NAME)

_stop_requested = threading.Event()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat(status: str = "healthy", note: str = "") -> None:
    """Best-effort POST to write_service. Never raises."""
    try:
        requests.post(
            HEARTBEAT_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service":        SERVICE_NAME,
                    "last_heartbeat": _now_iso(),
                    "status":         status,
                    "meta":           json.dumps({"note": note})[:500] if note else None,
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception:
        pass


def _heartbeat_loop() -> None:
    while not _stop_requested.is_set():
        _heartbeat("healthy")
        _stop_requested.wait(HEARTBEAT_SECS)


# ---------------------------------------------------------------------------
# Context bundle assembly
# ---------------------------------------------------------------------------

def _count_proposed() -> int:
    if not PROPOSED_DIR.exists():
        return 0
    return sum(
        1 for p in PROPOSED_DIR.glob("*.json")
        if not p.name.endswith(".done.json")
        and not p.name.endswith(".failed.json")
    )


def _try_import_layer1():
    """Layer 1 knowledge sources, same module the legacy generator uses."""
    try:
        sys.path.insert(0, str(SENTINEL_DIR))
        import directive_knowledge_sources as dks  # type: ignore
        return dks.assemble_layer1_context()
    except Exception as e:
        log.warning("Layer 1 unavailable: %s", e)
        return {
            "product_spec":  "[Layer 1 unavailable]",
            "wiring_map":    "[Layer 1 unavailable]",
            "gaps_map":      "[Layer 1 unavailable]",
            "quality_map":   "[Layer 1 unavailable]",
        }


def _try_load_schema() -> dict:
    try:
        sys.path.insert(0, str(SENTINEL_DIR))
        import sentinel_directive_generator as sdg  # type: ignore
        return sdg.load_schema()
    except Exception as e:
        log.warning("schema load failed: %s", e)
        return {}


def _query_recent_failures() -> list:
    """Read-only fetch of last 50 escalation/build failure rows."""
    sql = (
        "SELECT content, created_at FROM mesh_memory "
        "WHERE memory_type IN ('escalation_call', 'build_failure') "
        "AND created_at > NOW() - INTERVAL 24 HOUR "
        "ORDER BY created_at DESC LIMIT 50"
    )
    try:
        r = requests.get(WS_QUERY_URL, params={"sql": sql}, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning("ws_query failures: %s", e)
    return []


def _existing_modules() -> list:
    """Live-graph dedup signal: distinct module files already in the code graph,
    so the architect proposes NOVEL capabilities instead of re-proposing modules
    that already exist (the static read_already_built() set goes stale, which is why
    recently-built files keep getting re-proposed). Bus-routed (/query on :8772, NOT
    direct duckdb), best-effort -> [] on any error. One query per cycle, and only on
    an idle cycle (see _builder_idle), so it adds no lock/herd pressure."""
    # Precise dedup universe: distinct .py MODULE basenames only. The graph also
    # indexes directives/*.done.json, directives_archive/* and breaker_actions/* --
    # those are artifacts, not modules an architect would propose, so we exclude
    # them (drops ~1600 noise names, leaving ~1000 real modules). Basename DISTINCT
    # is done in-SQL so the cap bounds modules, not alpha-early full paths.
    sql = ("SELECT DISTINCT regexp_replace(source_file, '^.*/', '') AS mod "
           "FROM code_nodes "
           "WHERE source_file LIKE '%.py' "
           "AND source_file NOT LIKE 'directives/%' "
           "AND source_file NOT LIKE 'directives_archive/%' "
           "AND source_file NOT LIKE 'breaker_actions/%' "
           "ORDER BY mod LIMIT 1200")
    try:
        # POST json body -- matches the builder's proven ws_query transport,
        # which returns {"rows": [...]} (the shape parsed below). One serialized
        # read on the same :8772 writer; best-effort.
        r = requests.post(WS_QUERY_URL, json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("rows", []) if isinstance(data, dict) else data
            mods = sorted({str(row.get("mod", "")).strip()
                           for row in rows
                           if isinstance(row, dict) and row.get("mod")})
            return [m for m in mods if m]
    except Exception as e:
        log.warning("ws_query existing_modules: %s", e)
    return []


def _builder_idle() -> bool:
    """True when the BUILD side is quiet -- no NEW build_artifact emitted in the
    last IDLE_MIN minutes.

    Keys on build_artifact EMISSION (mesh_memory) NOT raw DIRECTIVE_* events: the
    edit-class wire_*/integrate_* directives emit DIRECTIVE_COMPLETE but produce NO
    build_artifact by design (declared_output is None). Keying on DIRECTIVE_* (the
    prior behaviour) let that constant edit churn read as "builder active" every
    cycle and DEFER generation indefinitely -> novel CREATE proposals starved
    (verified live 2026-06-14). A build_artifact row means a real CREATE build just
    landed, so defer for IDLE_MIN to keep the graph read + goose subprocess from
    contending with the active build chain (the anti-herd intent is preserved).

    The generator is a BATCH process. Bus-routed, best-effort; returns True (idle)
    on any error so a check fault never STARVES generation -- strictly no regression
    vs the prior fail-open behaviour."""
    sql = ("SELECT MAX(created_at) AS last_at FROM mesh_memory "
           "WHERE memory_type = 'build_artifact'")
    try:
        r = requests.post(WS_QUERY_URL, json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("rows", []) if isinstance(data, dict) else data
            last = rows[0].get("last_at") if rows and isinstance(rows[0], dict) else None
            if not last:
                return True
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0 >= IDLE_MIN
    except Exception as e:
        log.warning("ws_query idle-check: %s", e)
    return True


def _cap_layer1(l1):
    """Cap each layer1 knowledge-map field to LAYER1_FIELD_CAP chars so the base
    ctx stays small enough for the slow ladder architect to finish within the goose
    timeout. Best-effort: non-dict / non-str values pass through unchanged."""
    if not isinstance(l1, dict):
        return l1
    return {k: (v[:LAYER1_FIELD_CAP] if isinstance(v, str) else v) for k, v in l1.items()}


def build_context() -> dict:
    ctx = {
        "schema":            _try_load_schema(),
        "layer1":            _cap_layer1(_try_import_layer1()),
        "recent_failures":   _query_recent_failures(),
        "proposed_depth":    _count_proposed(),
        "generated_at":      _now_iso(),
    }
    # Fit the live-graph module list into whatever ctx budget remains so the
    # downstream json.dumps[:CTX_CAP] truncation never silently eats the list
    # tail (its at-risk end -- recently-built modules) OR a later field. We add
    # the modules LAST and trim them to the remaining bytes, not the recipe.
    mods = _existing_modules()
    if mods:
        used = len(json.dumps(ctx, default=str))
        budget = max(0, CTX_MODULE_BUDGET - used)
        kept, size = [], 0
        for m in mods:
            size += len(m) + 4            # quotes + comma overhead
            if size > budget:
                break
            kept.append(m)
        if len(kept) < len(mods):
            log.info("already_built_modules trimmed to %d/%d for ctx budget",
                     len(kept), len(mods))
        ctx["already_built_modules"] = kept
    else:
        ctx["already_built_modules"] = []
    return ctx


# ---------------------------------------------------------------------------
# Goose invocation
# ---------------------------------------------------------------------------

def _ensure_goose_env() -> None:
    """Match goose_runner.py's env preset so we share the shim."""
    os.environ.setdefault("GOOSE_PROVIDER",  "openai")
    os.environ.setdefault("GOOSE_MODEL",     "MiniMax-Text-01")
    os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:8796/v1")
    os.environ.setdefault("OPENAI_API_KEY",  "dummy_key_for_shim")


def run_goose_cycle() -> dict:
    """Invoke goose with the directive_architect recipe. Return summary dict."""
    if not RECIPE_PATH.exists():
        log.error("recipe missing: %s", RECIPE_PATH)
        return {"status": "skipped", "reason": "recipe_missing"}

    depth = _count_proposed()
    if depth >= MAX_PROPOSED:
        log.info("proposed/ depth %d >= cap %d; skipping cycle", depth, MAX_PROPOSED)
        return {"status": "skipped", "reason": "depth_cap", "depth": depth}

    # Batch-when-idle: only generate when the build side is quiet, so the graph
    # read + goose subprocess never contend with an active build. Herd-safe.
    if IDLE_GATE and not _builder_idle():
        log.info("builder active (build_artifact within %dm); deferring generation", IDLE_MIN)
        return {"status": "skipped", "reason": "builder_busy"}

    ctx = build_context()
    ctx_json = json.dumps(ctx, default=str)[:60000]   # MiniMax prompt cap headroom

    log.info("invoking goose (ctx %d bytes, proposed_depth=%d)", len(ctx_json), depth)
    _ensure_goose_env()
    try:
        proc = subprocess.run(
            ["goose", "run", "--recipe", str(RECIPE_PATH),
             "--params", f"context_json={ctx_json}"],
            capture_output=True,
            text=True,
            timeout=GOOSE_TIMEOUT,
            cwd=str(SENTINEL_DIR),
        )
        rc = proc.returncode
        # Goose writes its proposed/ JSONs via the directive_mcp tool calls;
        # we don't need to parse stdout. Just count what landed.
        new_depth = _count_proposed()
        delta = new_depth - depth
        if proc.stderr:
            log.warning("goose stderr[:300]: %s", proc.stderr[:300].replace("\n", " | "))
        log.info("goose returned rc=%d; proposed_depth %d -> %d (+%d)",
                 rc, depth, new_depth, delta)
        return {
            "status": "ok" if rc == 0 else "goose_nonzero",
            "rc": rc,
            "proposed_delta": delta,
            "proposed_depth_after": new_depth,
        }
    except subprocess.TimeoutExpired:
        log.error("goose TIMEOUT after %ds", GOOSE_TIMEOUT)
        return {"status": "timeout"}
    except FileNotFoundError:
        log.error("goose CLI not on PATH; daemon cannot continue")
        return {"status": "no_goose"}
    except Exception as e:
        log.error("goose invocation error: %s", e)
        return {"status": "error", "error": str(e)}


def _record_cycle(summary: dict) -> None:
    """Best-effort write_service log row for forensics."""
    try:
        requests.post(HEARTBEAT_URL, json={
            "table": "mesh_memory",
            "rows": [{
                "agent_id": SERVICE_NAME,
                "memory_type": "directive_generation_goose",
                "content": json.dumps(summary),
                "importance": 0.6,
                "created_at": _now_iso(),
            }],
            "wait": False,
        }, timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _handle_signal(signum, _frame):
    log.info("received signal %d; requesting stop", signum)
    _stop_requested.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("=" * 60)
    log.info("ZO-SENTINEL Directive Generator (Goose-driven) v0.1")
    log.info("  Recipe:        %s", RECIPE_PATH)
    log.info("  Writes to:     %s", PROPOSED_DIR)
    log.info("  Poll every:    %ds", POLL_SECS)
    log.info("  Heartbeat:     %ds", HEARTBEAT_SECS)
    log.info("  Goose timeout: %ds", GOOSE_TIMEOUT)
    log.info("  Proposed cap:  %d", MAX_PROPOSED)
    log.info("=" * 60)

    hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="hb")
    hb.start()

    # Immediate cycle on startup
    summary = run_goose_cycle()
    _record_cycle(summary)

    while not _stop_requested.is_set():
        _stop_requested.wait(POLL_SECS)
        if _stop_requested.is_set():
            break
        try:
            summary = run_goose_cycle()
            _record_cycle(summary)
        except Exception as e:
            log.error("cycle error: %s", e)

    log.info("clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
