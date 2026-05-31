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
from zo_sentinel.build_routing import build_env_for, resolve_directive_id  # noqa: E402

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
GOOSE_TIMEOUT = 600

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOGS_DIR = Path("/home/workspace/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "goose_runner.log"

SHARED_OUTPUTS = Path("/home/workspace/shared/outputs/goose")
TASK_FILE = Path("/tmp/goose_task.txt")

DIRECTIVES_PATH = Path("/home/workspace/zo_sentinel/directives")
PENDING_DIR = DIRECTIVES_PATH / "pending"
DONE_DIR = DIRECTIVES_PATH / "done"

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


def load_directives_from_mesh():
    """Load directives from BOTH mesh_memory DB and pending dir (merged)."""
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
                if not d.get("content") and d.get("goal"):
                    d["content"] = d["goal"]
                if not d.get("content") and d.get("spec"):
                    d["content"] = d["spec"]
                if not d.get("content"):
                    continue
                d = _parse_directive(d)
                did = str(d.get("directive_id", d.get("id", f.stem)))
                if did not in seen_ids:
                    seen_ids.add(did)
                    directives.append(d)
            except Exception as e:
                log(f"Failed to read {f.name}: {e}")

    log(f"Total directives loaded: {len(directives)} (DB + pending dir merged)")
    return directives

def is_goose_eligible(directive):
    """Check if directive is eligible for Goose execution."""
    content = directive.get("content", directive.get("description", ""))
    complexity = directive.get("complexity", "")
    source = directive.get("source", "")
    
    # Accept all directives -- Goose handles any complexity
    # Only skip if already built (idempotent check)
    directive_id_val = directive.get("directive_id") or directive.get("id", "unknown")
    done_file = Path("/home/workspace/zo_sentinel/directives") / f"{directive_id_val}.done.json"
    if done_file.exists():
        return False  # Already built
    eligible = True  # Accept all complexities
    
    # high complexity -> coworker handoff (handled separately in main loop)
    # low/medium/unknown -> Goose recipe
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

def run_goose_task(directive_id, content, extra_env=None):
    """Execute Goose on task file with timeout.

    extra_env (from build_routing.build_env_for) routes the architect
    (GOOSE_MODEL) + codegen (ZO_BUILD_TIER) up the ladder by complexity and
    carries task/phase for the build_artifact row builder_mcp emits."""
    log(f"Executing Goose for directive: {directive_id}")

    try:
        recipe_path = PROJECT_DIR / "goose_recipes" / "architect.yaml"
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

def call_minimax_fallback(directive):
    """Goose subprocess failed/unavailable -> route directive through the
    ladder shim (escalation.py, all 16 rungs). One HTTP call; the shim does
    MiniMax->Gemini->Gemma->Zo traversal internally."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    content = directive.get("content", directive.get("description", ""))
    log(f"Routing directive {directive_id} through ladder shim")
    try:
        resp = requests.post(SHIM_URL, json={
            "model": "zo-ladder-v1",
            "messages": [
                {"role": "system", "content": "You are an autonomous builder for zo-sentinel. Output only the completed code/results."},
                {"role": "user", "content": f"Complete this task: {content}"},
            ],
            "temperature": 0.2,
            "max_tokens": 8192,
        }, timeout=600)
        if resp.status_code == 200:
            txt = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
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
                
                # Try Goose first
                if goose_installed:
                    result = run_goose_task(directive_id, directive.get("content", ""),
                                            build_env_for(directive))
                    
                    if result.get("success"):
                        write_result(directive_id, True, result.get("stdout", ""))
                        mark_directive_completed(directive)
                    else:
                        # Goose failed - try fallback
                        log(f"Goose failed for {directive_id}: {result.get('error')}")
                        fallback_result = call_minimax_fallback(directive)
                        
                        if fallback_result.get("success"):
                            write_result(
                                directive_id, 
                                True, 
                                fallback_result.get("result", ""),
                                fallback_used=True
                            )
                            mark_directive_completed(directive)
                        else:
                            write_result(
                                directive_id,
                                False,
                                error=fallback_result.get("error", "All methods failed"),
                                fallback_used=True
                            )
                            # Requeue to zo_builder
                            try:
                                ws_write("mesh_events", {
                                    "event_type": "directive",
                                    "content": directive.get("content", ""),
                                    "complexity": "medium",
                                    "source": "zo_builder",
                                    "retry_count": 1,
                                    "created_at": get_utc_now()
                                })
                                log(f"Requeued to zo_builder: {directive_id}")
                            except Exception as e:
                                log(f"Failed to requeue: {e}")
                else:
                    # Goose not installed - go straight to fallback
                    fallback_result = call_minimax_fallback(directive)
                    
                    if fallback_result.get("success"):
                        write_result(
                            directive_id,
                            True,
                            fallback_result.get("result", ""),
                            fallback_used=True
                        )
                        mark_directive_completed(directive)
                    else:
                        write_result(
                            directive_id,
                            False,
                            error=fallback_result.get("error", "All methods failed"),
                            fallback_used=True
                        )
                
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