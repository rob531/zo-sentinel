#!/usr/bin/env python3
"""
build_watchdog.py  v2.0.0

v2.0.0:
  - zo_architect escalation tier via Haiku API (CPU-safe: no 8B local weight swap)
  - VRAM/endpoint swap: Ollama keep_alive=0 evicts coder, Haiku fills architect role
  - zo-sentinel-debugger model for retry 1-2 (CoT permitted, code extracted via ---CODE---)
  - zo-sentinel-coder for initial generation (raw code only)
  - Dynamic AST dependency graph replaces static DEPENDENTS dict
  - Escalation stores all 3 tracebacks in combined escalation prompt

Escalation ladder:
  Attempt 1-2:  zo-sentinel-debugger (llama3.2:3b + CoT, local free)
  Attempt 3:    zo_architect (Haiku via InferenceRouter, ~$0.01, 2-5s)
  Attempt 4+:   emit build_max_retries, stop
"""

import sys, os, ast, json, time, logging, subprocess, requests, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_DIR    = Path("/home/workspace/zo_sentinel")
MESH_DIR       = Path("/home/workspace/zo_mesh")
LOGS_DIR       = Path("/home/workspace/logs")
WRITE_SERVICE  = "http://127.0.0.1:8772"
OLLAMA_URL     = "http://127.0.0.1:11434"
INFERENCE_URL  = "http://127.0.0.1:8773/complete"
POLL_SECS      = 60
COOLDOWN_SECS  = 600
MAX_RETRIES    = 4           # attempt 1-2 debugger, attempt 3 architect, 4 = give up
ARCHITECT_AT   = 3          # escalate to architect on this attempt number

# zo_architect = Haiku via InferenceRouter (CPU-safe, no local weight blowout)
# Falls back to qwen2.5-coder:1.5b if API unavailable
ARCHITECT_MODEL_API   = "claude-haiku-4-5-20251001"   # via InferenceRouter
ARCHITECT_MODEL_LOCAL = "qwen2.5-coder:1.5b"         # local fallback
CODER_MODEL    = "zo-sentinel-coder"
DEBUGGER_MODEL = "zo-sentinel-debugger"

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(str(LOGS_DIR / "build_watchdog.log")),
        logging.StreamHandler(sys.stdout)
    ],
    format="%(asctime)s [watchdog] %(levelname)s: %(message)s"
)
log = logging.getLogger("watchdog")


# ── State ─────────────────────────────────────────────────────────────
_last_retry:    dict = {}
_retry_count:   dict = {}
_traceback_log: dict = {}   # filename -> list of tracebacks across attempts
_seen_event_ids: set = set()
_phase4_emitted: bool = False
_dep_graph:     dict = {}   # {filename: [files_that_import_it]} -- rebuilt dynamically


# ── Dynamic AST Dependency Graph ─────────────────────────────────────────────

def build_dependency_graph(workspace_path: Path) -> dict:
    """
    Dynamically builds reverse dependency map: {file: [files_that_import_it]}
    Parses every .py file in workspace via AST. Skips syntax-broken files.
    Called after every build_complete event so the graph stays current.
    """
    graph = {}
    for py_file in workspace_path.glob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        key = f"{alias.name}.py"
                        graph.setdefault(key, [])
                        if py_file.name not in graph[key]:
                            graph[key].append(py_file.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    key = f"{node.module}.py"
                    graph.setdefault(key, [])
                    if py_file.name not in graph[key]:
                        graph[key].append(py_file.name)
        except SyntaxError:
            pass  # Broken files excluded — can't trust their imports anyway
        except Exception:
            pass
    return graph


def get_dependents(filename: str) -> list:
    """Return files that import the given filename (from live graph)."""
    return _dep_graph.get(filename, [])


# ── Mesh helpers ────────────────────────────────────────────────────────────

def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=8)
        if r.status_code == 200: return r.json().get("rows", [])
    except Exception: pass
    return []

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": row, "wait": True}, timeout=8)
        return r.status_code == 200
    except Exception: return False

def heartbeat():
    ws_write("service_health", {
        "service": "build_watchdog",
        "last_heartbeat": datetime.now(timezone.utc).isoformat()
    })

def mesh_event(event_type: str, payload: dict, severity: str = "INFO"):
    ws_write("mesh_events", {
        "agent_id": "t1.build_watchdog", "event_type": event_type, "tier": "T1",
        "payload": json.dumps(payload), "severity": severity,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

def run_cmd(cmd: list, desc: str, timeout: int = 120) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(PROJECT_DIR), timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"TimeoutExpired ({timeout}s)"
    except Exception as e:
        return 1, "", str(e)


# ── Ollama model management ───────────────────────────────────────────────────────

def evict_model(model: str):
    """
    Evict a model from Ollama RAM by setting keep_alive=0.
    Frees memory for the architect tier before escalation.
    On CPU hardware this releases RAM bandwidth rather than VRAM.
    """
    try:
        requests.post(f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=10)
        log.info(f"  Evicted {model} from Ollama RAM")
    except Exception as e:
        log.warning(f"  evict_model {model}: {e}")


# ── CoT code extraction ───────────────────────────────────────────────────────────

def extract_code_from_debugger_output(output: str) -> tuple:
    """
    zo-sentinel-debugger outputs:
      [analysis paragraph]
      ---CODE---
      [complete Python file]

    Splits on ---CODE--- separator, returns (analysis, code).
    If separator missing, treats entire output as code (graceful degradation).
    Also strips any markdown fences the model sneaks in.
    """
    if "---CODE---" in output:
        parts    = output.split("---CODE---", 1)
        analysis = parts[0].strip()
        code     = parts[1].strip()
    else:
        analysis = ""
        code     = output.strip()

    # Strip markdown fences
    code = re.sub(r'^```python\n', '', code, flags=re.MULTILINE)
    code = re.sub(r'^```\n?',     '', code, flags=re.MULTILINE)
    code = re.sub(r'\n?```$',    '', code, flags=re.MULTILINE)

    return analysis, code.strip()


# ── zo_architect escalation ──────────────────────────────────────────────────────

def escalate_to_architect(filename: str, original_description: str) -> bool:
    """
    Escalation ladder step 3: route to Haiku via InferenceRouter.

    On CPU hardware, a local 8B model would take 400-800s per generation.
    Haiku via API: 2-5s, better code quality than any local 7B/8B model.
    Evict local coder first to free RAM, restore after.

    Combines all accumulated tracebacks from prior attempts into one
    comprehensive prompt so the architect has full context.
    """
    log.info(f"  ESCALATING to zo_architect for {filename}")
    mesh_event("build_escalation", {
        "file":     filename,
        "model":    ARCHITECT_MODEL_API,
        "attempts": _retry_count.get(filename, 0)
    })

    # Evict local coder to free RAM before architect call
    evict_model(CODER_MODEL)
    evict_model(DEBUGGER_MODEL)

    # Build combined traceback context from all prior attempts
    prior_tbs = _traceback_log.get(filename, [])
    tb_section = ""
    for i, tb in enumerate(prior_tbs[-3:], 1):  # max 3 prior tracebacks
        clipped = "\n".join(tb.splitlines()[-8:])
        tb_section += f"\n=== Attempt {i} traceback ===\n{clipped}\n"

    # Read current (broken) file tail
    file_path = PROJECT_DIR / filename
    code_tail = ""
    if file_path.exists():
        lines = file_path.read_text().splitlines()
        code_tail = "\n".join(lines[-20:])

    architect_prompt = (
        f"You are a senior Python engineer specialising in FastAPI, DuckDB, and "
        f"async daemon architecture. A junior model failed {len(prior_tbs)} times "
        f"to generate {filename}. You must write the definitive, production-ready version.\n\n"
        f"TASK: {original_description[:600]}\n\n"
        f"FAILED CODE (last 20 lines):\n{code_tail}\n"
        f"\nPRIOR TRACEBACKS:{tb_section}\n"
        f"CRITICAL RULES:\n"
        f"- write_service is an HTTP endpoint: POST http://127.0.0.1:8772/write "
        f"  json={{'table':t,'rows':{{...}},'wait':True}} -- 'rows' NOT 'row'\n"
        f"- No executescript(), no INSERT OR IGNORE, no DuckDB direct import\n"
        f"- Every daemon needs run() + if __name__=='__main__': run()\n"
        f"- Output ONLY raw Python. No markdown. No explanation.\n"
    )

    # Route to Haiku via InferenceRouter
    try:
        r = requests.post(INFERENCE_URL,
            json={
                "task_type":  "generate",
                "prompt":     architect_prompt,
                "agent_id":   "t1.build_watchdog.architect",
                "max_tokens": 4000,
                "model":      ARCHITECT_MODEL_API   # hint to router
            }, timeout=60)
        if r.status_code == 200:
            d = r.json()
            code = (d.get("text") or d.get("response") or "").strip()
            tier = d.get("tier_used", "?")
            log.info(f"  Architect response: tier={tier} {len(code)} bytes")
            if len(code) > 300:
                _write_generated_file(filename, code)
                return True
    except Exception as e:
        log.warning(f"  Architect API call failed: {e}")

    # Local fallback: qwen2.5-coder:1.5b (max viable on CPU)
    log.info(f"  Architect API failed — trying local {ARCHITECT_MODEL_LOCAL}")
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate",
            json={"model": ARCHITECT_MODEL_LOCAL,
                  "prompt": architect_prompt,
                  "stream": False,
                  "options": {"num_ctx": 4096}},
            timeout=600)   # 10 min budget for local 1.5b on CPU
        if r.status_code == 200:
            code = r.json().get("response", "").strip()
            if len(code) > 300:
                _write_generated_file(filename, code)
                return True
    except Exception as e:
        log.warning(f"  Local architect failed: {e}")

    return False


def _write_generated_file(filename: str, code: str):
    """Atomic write generated code to project dir."""
    from pathlib import Path
    import shutil, tempfile
    path = PROJECT_DIR / filename
    tmp  = path.parent / f".{path.name}.architect.tmp"
    tmp.write_text(code)
    shutil.move(str(tmp), str(path))
    log.info(f"  Architect wrote: {path} ({len(code)} bytes)")


# ── Smoke test runner ───────────────────────────────────────────────────────────

def smoke_test_file(filename: str) -> tuple:
    """
    Run smoke_test.py --write-db on a single file.
    Returns (passed: bool, traceback: str)
    """
    rc, stdout, stderr = run_cmd(
        [sys.executable, "tests/smoke_test.py", filename, "--write-db"],
        f"smoke_test {filename}", timeout=30
    )
    output = (stdout + stderr).strip()
    passed = rc == 0
    tb     = "" if passed else "\n".join(output.splitlines()[-10:])
    log.info(f"  Smoke {'PASS' if passed else 'FAIL'}: {filename}")
    if not passed and tb:
        log.warning(f"    {tb[:200]}")
    return passed, tb


# ── Task description lookup ───────────────────────────────────────────────────────

TASK_DESCRIPTIONS = {
    "approval_workflow.py":  "FastAPI approval workflow on port 8780. POST /api/submit, POST /api/decision/{id}, GET /api/registry, GET /api/audit. All DB writes via requests.post('http://127.0.0.1:8772/write', json={'table':t,'rows':r,'wait':True}).",
    "signal_analyser.py":    "T2 daemon scoring MCP servers on 6 signals (domain_trust, tool_description_safety, permission_scope, supply_chain, community_signal, temporal_stability 0-100). ws_query mcp_server_registry, ws_write mcp_signal_scores. 30min daemon with heartbeat.",
    "trust_synthesiser.py":  "T3 daemon computing composite trust_score (6 weighted signals). Maps score to verdict TRUSTED_GENERAL|TRUSTED_RESEARCH|ENTERPRISE_CONTROLLED|CAUTION_LIMITED|HIGH_RISK_ISOLATED|KNOWN_THREAT|INSUFFICIENT. 30min daemon.",
    "mcp_scanner.py":        "T1 crawler: npm @modelcontextprotocol/* + GitHub topic:mcp-server. ws_write to mcp_server_registry. 6hr daemon.",
    "registry_api.py":       "FastAPI REST on port 8781. GET /v1/assess?mcp=, GET /v1/registry, GET /v1/threats, GET /health.",
    "rug_pull_monitor.py":   "Monitors approved MCPs for tool definition mutations via SHA256 hash comparison. ws_write to mcp_threat_associations on change. 6hr daemon.",
    "policy_engine.py":      "evaluate_policy(submission, trust_score, verdict, signals) -> BLOCK|ESCALATE|ALLOW. Reads mcp_policy_rules via ws_query.",
}


# ── Retry injection ────────────────────────────────────────────────────────────

def handle_failure(filename: str, traceback: str = ""):
    """
    Full failure handling pipeline.
    Decides whether to use debugger, coder, or architect based on attempt count.
    """
    count = _retry_count.get(filename, 0)

    # Accumulate tracebacks across attempts
    if traceback:
        if filename not in _traceback_log:
            _traceback_log[filename] = []
        _traceback_log[filename].append(traceback)

    # Check cooldown
    last = _last_retry.get(filename)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < COOLDOWN_SECS:
        log.info(f"  Cooldown active for {filename}")
        return

    if count >= MAX_RETRIES:
        log.warning(f"  MAX RETRIES ({MAX_RETRIES}) for {filename} — manual review needed")
        mesh_event("build_max_retries", {"file": filename, "retries": count}, severity="WARNING")
        return

    desc  = TASK_DESCRIPTIONS.get(filename, f"Rebuild {filename} correctly")
    task  = f"retry_{filename.replace('.py','')}_attempt{count+1}"

    # Escalation decision
    if count >= ARCHITECT_AT - 1:
        log.info(f"  Attempt {count+1}: escalating to zo_architect")
        success = escalate_to_architect(filename, desc)
        if success:
            passed, new_tb = smoke_test_file(filename)
            if passed:
                _retry_count[filename] = 0
                log.info(f"  ARCHITECT SUCCESS: {filename}")
                mesh_event("build_architect_success", {"file": filename})
                return
            else:
                _traceback_log.setdefault(filename, []).append(new_tb)
        # If architect also fails, fall through to max retry
        _retry_count[filename] = MAX_RETRIES
        return

    # Attempt 1-2: zo-sentinel-debugger (CoT + code extraction)
    # Inject via inject_directive.py with debugger model hint
    model_hint = DEBUGGER_MODEL
    log.info(f"  Attempt {count+1}: inject retry via {model_hint}")

    cmd = [
        sys.executable, "inject_directive.py",
        "--retry",
        "--file",        filename,
        "--task",        task,
        "--description", desc,
        "--complexity",  "medium",
    ]
    # Pass any dependency reads
    from build_watchdog import PHASE4_FILES  # avoid circular -- use inline
    deps = [
        dep for dep in [
            "schema.py", "schema_v2.py", "known_threats.py"
        ] if (PROJECT_DIR / dep).exists() and dep != filename
    ]
    if deps:
        cmd += ["--reads"] + deps

    rc, stdout, _ = run_cmd(cmd, f"inject_retry {filename} via {model_hint}")
    _last_retry[filename]  = datetime.now(timezone.utc)
    _retry_count[filename] = count + 1

    if rc == 0:
        mesh_event("build_retry_injected", {
            "file": filename, "attempt": count + 1, "model": model_hint
        })
    else:
        log.error(f"  inject_directive failed for {filename}")


# ── Regression guard ────────────────────────────────────────────────────────────

def check_dependents(filename: str):
    """Re-validate files that import the newly-built file (live AST graph)."""
    dependents = get_dependents(filename)
    if not dependents:
        return
    log.info(f"  Regression check: {filename} -> {dependents}")
    for dep in dependents:
        if not (PROJECT_DIR / dep).exists():
            continue
        passed, tb = smoke_test_file(dep)
        if not passed:
            log.warning(f"  REGRESSION: {dep} broke after {filename} rebuilt")
            mesh_event("build_regression", {"file": dep, "caused_by": filename}, severity="WARNING")
            handle_failure(dep, tb)


# ── Phase 4 gate ────────────────────────────────────────────────────────────

PHASE4_FILES = ["approval_workflow.py", "schema_v2.py"]

def check_phase4_gate():
    global _phase4_emitted
    if _phase4_emitted: return
    passed, failed = [], []
    for fname in PHASE4_FILES:
        path = PROJECT_DIR / fname
        if path.exists():
            ok, _ = smoke_test_file(fname)
            (passed if ok else failed).append(fname)
        else:
            failed.append(fname)
    if not failed:
        _phase4_emitted = True
        log.info("Phase 4 CHECKPOINT: PASS")
        mesh_event("phase_checkpoint", {
            "phase": "4", "status": "PASS",
            "passed": passed, "failed": []
        })
    else:
        log.info(f"Phase 4 gate: {len(passed)}/{len(PHASE4_FILES)} ({failed} pending)")


# ── Event polling ────────────────────────────────────────────────────────────

def poll_events():
    global _dep_graph
    rows = ws_query(
        "SELECT id, event_type, payload, created_at "
        "FROM mesh_events "
        "WHERE agent_id = 't1.zo_sentinel_builder' "
        "AND event_type IN ('build_failed', 'build_complete') "
        "AND created_at > now() - INTERVAL 10 MINUTE "
        "ORDER BY created_at ASC LIMIT 20"
    )
    for row in rows:
        eid = row.get("id")
        if eid in _seen_event_ids: continue
        _seen_event_ids.add(eid)
        etype = row.get("event_type")
        try:
            payload = json.loads(row.get("payload", "{}"))
        except Exception: continue

        if etype == "build_failed":
            fname = Path(payload.get("file", "")).name or ""
            if not fname: continue
            log.info(f"Event: build_failed -> {fname}")
            passed, tb = smoke_test_file(fname)
            if not passed:
                handle_failure(fname, tb)

        elif etype == "build_complete":
            fname = Path(payload.get("file", "")).name or ""
            if not fname: continue
            log.info(f"Event: build_complete -> {fname}")
            _retry_count[fname] = 0

            # Rebuild live dependency graph
            _dep_graph = build_dependency_graph(PROJECT_DIR)
            log.info(f"  Dep graph rebuilt: {len(_dep_graph)} upstream nodes")

            # Regression guard using live graph
            check_dependents(fname)

            # Phase 4 gate
            if fname in PHASE4_FILES:
                check_phase4_gate()


# ── Main loop ───────────────────────────────────────────────────────────────────

def run():
    global _dep_graph
    log.info("=" * 60)
    log.info("Build Watchdog v2.0.0")
    log.info(f"  Escalation: attempts 1-{ARCHITECT_AT-1}=debugger, {ARCHITECT_AT}=architect (Haiku API)")
    log.info(f"  Architect local fallback: {ARCHITECT_MODEL_LOCAL}")
    log.info(f"  Eviction: keep_alive=0 before architect call")
    log.info(f"  Dep graph: dynamic AST (rebuilt on every build_complete)")
    log.info("=" * 60)

    # Build initial dependency graph
    _dep_graph = build_dependency_graph(PROJECT_DIR)
    log.info(f"  Initial dep graph: {len(_dep_graph)} upstream nodes")

    heartbeat()
    check_phase4_gate()

    while True:
        time.sleep(POLL_SECS)
        try:
            heartbeat()
            poll_events()
        except Exception as e:
            log.error(f"Watchdog cycle: {e}")


if __name__ == "__main__":
    run()