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
GOOSE_TIMEOUT   = int(os.environ.get("DGG_GOOSE_TIMEOUT", 240))   # was 480; a converging cycle is fast, a tool-call LOOP just burns -- fail it sooner
MAX_PROPOSED    = int(os.environ.get("DGG_MAX_PROPOSED_DEPTH", 40))
# Architect-scoped goose BINARY -- lets the architect run a DIFFERENT goose
# version from the builder (goose_runner.py, which keeps bare `goose` on PATH).
# Default "goose" = today (both roles share the PATH binary). Point this at a
# second install (e.g. /usr/local/bin/goose-1.38) to upgrade ONLY the architect
# -- builder blast-radius stays zero. Reversible: unset -> back to PATH goose.
ARCHITECT_GOOSE_BIN = os.environ.get("ZO_ARCHITECT_GOOSE_BIN", "goose")
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


_DONE_DIR = Path(os.environ.get(
    "DGG_DONE_DIR", "/home/workspace/zo_sentinel/directives/done"))


def _recent_built_modules(limit: int = 600) -> list:
    """Module basenames of the most-recently-built directives (directives/done/*.json
    by mtime, newest first). The architect keeps re-proposing RECENT builds because the
    graph dedup list is ordered alphabetically and the budget trim buries recents past
    the cutoff. Surfacing these FIRST in the budgeted avoid-list is the targeted fix for
    'recently-built files keep getting re-proposed'. Best-effort -> [] on any error."""
    try:
        files = sorted(_DONE_DIR.glob("*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    except Exception:
        return []
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            of = (d.get("output_file") or "").strip()
            if of:
                out.append(of.rsplit("/", 1)[-1])   # basename, matches graph form
        except Exception:
            continue
    return out


def _recent_proposals(days: int = 3, cap: int = 25) -> list:
    """The architect's OWN recent proposals (mesh_memory directive_proposed), newest
    first, de-duplicated -- its cross-cycle COVERAGE memory, INCLUDING proposals since
    rejected/dropped that no longer sit in proposed/. Lets it stop re-circling the same
    subjects and range elsewhere. Bus-routed, best-effort -> []."""
    sql = ("SELECT content FROM mesh_memory WHERE memory_type='directive_proposed' "
           f"AND created_at > NOW() - INTERVAL {int(days)} DAY "
           "ORDER BY created_at DESC LIMIT 120")
    try:
        r = requests.post(WS_QUERY_URL, json={"sql": sql}, timeout=8)
        rows = r.json().get("rows", []) if r.ok else []
    except Exception:
        return []
    seen, out = set(), []
    for row in rows:
        try:
            task = json.loads(row.get("content") or "{}").get("task")
        except Exception:
            task = None
        if task and task not in seen:
            seen.add(task); out.append(task)
        if len(out) >= cap:
            break
    return out


def build_context() -> dict:
    ctx = {
        "schema":            _try_load_schema(),
        "layer1":            _cap_layer1(_try_import_layer1()),
        "recent_failures":   _query_recent_failures(),
        "proposed_depth":    _count_proposed(),
        "recent_proposals":  _recent_proposals(),
        "generated_at":      _now_iso(),
    }
    # Fit the live-graph module list into whatever ctx budget remains so the
    # downstream json.dumps[:CTX_CAP] truncation never silently eats the list
    # tail (its at-risk end -- recently-built modules) OR a later field. We add
    # the modules LAST and trim them to the remaining bytes, not the recipe.
    # Recency-first: the architect re-proposes RECENTLY-built modules, so put the
    # newest builds at the head of the budgeted avoid-list -- the alphabetical graph
    # order buried recents past the budget cutoff, leaving the architect blind to them.
    graph_mods = _existing_modules()
    recent = _recent_built_modules()
    _seen, mods = set(), []
    for _m in recent + graph_mods:
        if _m and _m not in _seen:
            _seen.add(_m); mods.append(_m)
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
    """Select the ARCHITECT's goose provider (architect-scoped, so the builder
    goose_runner stays on the shim ladder regardless).

    Default ``openai`` = the API-key ladder shim on :8796 (today's behavior --
    builder + architect share it). Set ZO_ARCHITECT_PROVIDER to an OAuth-subscription
    provider (e.g. ``gemini_oauth``) to point ONLY the architect at a stronger,
    higher-context model via your own cached OAuth session -- goose handles that
    auth itself, so no base_url/key is needed (and the shim is bypassed for this
    role). Reversible: unset the var -> back to the shim. The OAuth session must be
    pre-authenticated once (``goose configure`` -> Google Gemini) and must refresh
    non-interactively for the headless daemon."""
    prov = os.environ.get("ZO_ARCHITECT_PROVIDER", "openai").strip() or "openai"
    os.environ["GOOSE_PROVIDER"] = prov
    if prov == "openai":
        # Architect-scoped MODEL on the ladder shim. Default MiniMax-Text-01 (rung 0, weak)
        # -- the +0/fixation bottleneck. Set ZO_ARCHITECT_MODEL to a ladder alias to give the
        # architect a STRONGER rung via the shim WITHOUT OAuth (Gemini OAuth is dead on this
        # headless host). Recommended: zo-ladder-cerebras = Cerebras gpt-oss-120b (free,
        # tool-calling, the bake-off winner) with free-rung failover. Reversible: unset -> MiniMax.
        os.environ["GOOSE_MODEL"] = os.environ.get("ZO_ARCHITECT_MODEL", "MiniMax-Text-01").strip() or "MiniMax-Text-01"
        os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:8796/v1")
        os.environ.setdefault("OPENAI_API_KEY",  "dummy_key_for_shim")  # set => goose skips keyring

    # When the architect runs a NON-default (versioned) goose binary, give it an
    # ISOLATED config/data home. A different goose version PANICS at startup
    # (`thread 'sq...'`) reading the ~/.config/goose + session store the builder's
    # goose wrote -- version skew on the on-disk store. The canary proved 1.38 runs
    # clean against a FRESH store, so we replicate that: point this goose at its own
    # XDG dirs (env-driven provider config still applies; no config file needed).
    if ARCHITECT_GOOSE_BIN != "goose":
        iso = os.environ.get("ZO_ARCHITECT_GOOSE_HOME", "/home/workspace/.goose_architect")
        try:
            os.makedirs(iso, exist_ok=True)
        except Exception:
            pass
        os.environ["HOME"]            = iso
        os.environ["XDG_CONFIG_HOME"] = f"{iso}/.config"
        os.environ["XDG_DATA_HOME"]   = f"{iso}/.local/share"
        os.environ["XDG_STATE_HOME"]  = f"{iso}/.local/state"


def _emit_nonconvergence(secs, delta, rc, kind: str) -> None:
    """LOUD failure when an architect cycle does NOT converge to a directive (timeout, or
    rc=0 but +0). The model burned ~secs of tool calls without landing a propose_directive
    -- a goose tool-call LOOP / over-exploration, NOT a silent timeout. Emits a
    failure_classifier-catchable log line + a best-effort mesh_memory row so loop_watch /
    pipeline-watch surface it. (Stop + emit-a-failure-log, per the convergence-guard design.)"""
    model = os.environ.get("GOOSE_MODEL", "?")
    log.error("ARCHITECT NON-CONVERGENCE (%s): goose [%s] model=%s ran ~%ss, proposed +%d "
              "-- did NOT reach propose_directive (tool-call loop / over-exploration); rc=%s",
              kind, ARCHITECT_GOOSE_BIN, model, secs, max(delta or 0, 0), rc)
    try:
        import requests
        requests.post("http://127.0.0.1:8772/write",
                      json={"table": "mesh_memory", "rows": [{
                          "agent_id": "directive_architect",
                          "memory_type": "directive_gen_failure",
                          "content": json.dumps({"kind": kind, "secs": secs,
                                                 "delta": delta, "rc": rc, "model": model,
                                                 "bin": ARCHITECT_GOOSE_BIN}),
                          "created_at": _now_iso()}], "wait": False},
                      timeout=5)
    except Exception:
        pass


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

    log.info("invoking goose [%s] (ctx %d bytes, proposed_depth=%d)", ARCHITECT_GOOSE_BIN, len(ctx_json), depth)
    _ensure_goose_env()
    try:
        proc = subprocess.run(
            [ARCHITECT_GOOSE_BIN, "run", "--recipe", str(RECIPE_PATH),
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
        if delta <= 0:
            _emit_nonconvergence(GOOSE_TIMEOUT, delta, rc, "zero_proposed")
        return {
            "status": "ok" if rc == 0 else "goose_nonzero",
            "rc": rc,
            "proposed_delta": delta,
            "proposed_depth_after": new_depth,
        }
    except subprocess.TimeoutExpired:
        log.error("goose TIMEOUT after %ds", GOOSE_TIMEOUT)
        _emit_nonconvergence(GOOSE_TIMEOUT, 0, None, "timeout")
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
