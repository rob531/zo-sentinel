# goose_runner_lib/core.py -- PART OF goose_runner.py, not a standalone module.
# utility functions, heartbeat, ws I/O, directive management, goose eligibility check.
# goose_runner.py runs this file via _goose_runner_lib_exec("core") INSIDE
# goose_runner's own namespace, at the exact position this code stood
# (original lines 107-472), so every name here is still
# goose_runner.<name> and __file__ is goose_runner.py.
# Do not import it directly; callers keep citing goose_runner.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: goose_runner_lib/README.md)
from __future__ import annotations

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

def check_single_instance(*_args, **_kwargs):
    """Single-instance lock, identity-verified. See singleton_lock.py.

    Was: os.kill(pid, 0) -- "does SOME process own this number?" That let a
    recycled PID wedge this daemon shut permanently (2026-09-21, gh#5412).
    """
    _svc = globals().get("SERVICE_NAME") or os.path.splitext(
        os.path.basename(__file__))[0]
    return singleton_lock.check_single_instance(_svc, script=__file__)

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
        if f.name.startswith((".bak", ".duplicate")):  # backup/dup copies mask the queue (FU-020)
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


def topup_quarantine():
    """Top the directive queue up from the #4070 quarantine when it runs thin.

    improvement-loop cycle-0087. tools/requeue_quarantined.py implements the
    paced re-emission for GH #4079 and was measured DARK -- built, CI-tested,
    and called by NOTHING for 14 days, because its own docstring asked a cron,
    a lane or a human to remember to run it. This is the remembering, in code.

    Fail-soft and non-blocking by construction: the tool reads the whole repo
    to decide eligibility (minutes, not seconds), so quarantine_topup spawns it
    detached and returns at once, and it swallows every exception -- a top-up
    that can stall or kill the builder is worse than no top-up. Disable with
    ZO_QUARANTINE_TOPUP=0.
    """
    try:
        # By FILE PATH, not `from tools.quarantine_topup import ...`: this file
        # already wraps `from tools.uv_gate_runner import run_gates` because
        # tools/ is NOT importable in every launch context, and a seam that
        # silently degrades to a no-op is the exact defect this cycle is
        # closing. Resolved relative to THIS file, so it follows the daemon.
        import importlib.util as _ilu
        _src = Path(__file__).resolve().parent / "tools" / "quarantine_topup.py"
        _spec = _ilu.spec_from_file_location("quarantine_topup", _src)
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["quarantine_topup"] = _mod
        _spec.loader.exec_module(_mod)
        spawned, why = _mod.topup(PENDING_DIR)
        if spawned:
            log(f"quarantine top-up: {why}")
    except Exception as e:                                      # noqa: BLE001
        log(f"quarantine top-up unavailable (non-fatal): {e}")


def park_orphan_chains_safe():
    """Self-heal the pending/ queue each cycle: park directives whose `requires`
    name a file that no live pending directive produces (orphaned chain roots) so
    the queue cannot silently JAM -- the 2026-10-29 ~15h stall, where orphaned
    contract/router/service.toml dependents (their `logic` root never generated)
    filled pending/, so it never ran thin, so topup never refilled, so nothing
    built. Deterministic + reversible (parked dirs are revived once buildable).

    Fail-soft + flag-gated (ZO_ORPHAN_PARK=0 disables): resolved by FILE PATH like
    topup_quarantine, since tools/ is not importable in every launch context, and a
    seam-audit that silently degrades to a no-op is the defect it exists to close."""
    if os.environ.get("ZO_ORPHAN_PARK", "1") in ("0", "false", "False", "no", "NO"):
        return
    try:
        import importlib.util as _ilu
        _src = Path(__file__).resolve().parent / "tools" / "park_orphan_chain_directives.py"
        _spec = _ilu.spec_from_file_location("park_orphan_chain_directives", _src)
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["park_orphan_chain_directives"] = _mod
        _spec.loader.exec_module(_mod)
        _mod.park_orphan_chains(apply=True, log=log)
    except Exception as e:                                       # noqa: BLE001
        log(f"orphan-park unavailable (non-fatal): {e}")


def load_directives_from_mesh():
    """Load directives from BOTH mesh_memory DB and pending dir (merged)."""
    prune_done_pending()   # keep pending/ from growing unbounded with done files
    park_orphan_chains_safe()  # un-jam: park dependents whose chain-root has no producer
    topup_quarantine()     # keep the queue non-empty from the #4079 backlog
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
            if f.name.startswith((".bak", ".duplicate")):  # backup/dup copies mask the queue (FU-020)
                continue
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
