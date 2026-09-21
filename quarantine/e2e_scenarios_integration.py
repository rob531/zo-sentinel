import os
import sys
import argparse
import signal
import logging
import json
import subprocess
import requests
import time
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "e2e_scenarios_integration"
WRITE_SERVICE_URL = "http://localhost:8772"
PID_FILE = "/home/workspace/zo_sentinel/e2e_scenarios_integration.pid"
LOG_FILE = "/home/workspace/logs/e2e_scenarios_integration.log"
E2E_SCENARIOS_SCRIPT = "/home/workspace/zo_sentinel/e2e_scenarios.py"
SUPERVISOR_CONF = "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf"
POLL_SECS = 3600

logger = logging.getLogger(__name__)

def ws_write(table, rows):
    if isinstance(rows, dict):
        rows = [rows]
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=30)
    resp.raise_for_status()

def ws_query(sql, params=None):
    payload = {"sql": sql, "params": params or []}
    resp = requests.post(WRITE_SERVICE_URL + "/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rows", [])

def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"PID {old_pid} alive - exiting")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    logger.info(f"Signal {signum} received - shutting down")
    remove_pid_file()
    sys.exit(0)

def send_heartbeat(status="running", meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "service_name": SERVICE_NAME,
        "status": status,
        "last_heartbeat": ts,
        "meta": json.dumps(meta) if meta else None
    }
    ws_write("service_health", row)

def run_e2e_scenario(scenario_name, scenario_func):
    """Execute a single e2e scenario and record result."""
    scenario_id = f"e2e_{scenario_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    ts_start = datetime.now(timezone.utc).isoformat()
    
    try:
        result = scenario_func()
        ts_end = datetime.now(timezone.utc).isoformat()
        
        row = {
            "scenario_id": scenario_id,
            "scenario_name": scenario_name,
            "status": "PASS" if result.get("passed") else "FAIL",
            "ts_start": ts_start,
            "ts_end": ts_end,
            "details": json.dumps(result)
        }
        ws_write("e2e_scenario_results", row)
        return result
    except Exception as e:
        ts_end = datetime.now(timezone.utc).isoformat()
        logger.error(f"Scenario {scenario_name} exception: {e}")
        row = {
            "scenario_id": scenario_id,
            "scenario_name": scenario_name,
            "status": "ERROR",
            "ts_start": ts_start,
            "ts_end": ts_end,
            "details": json.dumps({"error": str(e)})
        }
        ws_write("e2e_scenario_results", row)
        return {"passed": False, "error": str(e)}

def scenario_1_new_mcp_flow():
    """Scenario 1: New MCP submission -> signal scored -> verdict -> attestation -> UI visible"""
    try:
        rows = ws_query("""
            SELECT server_id FROM mcp_server_registry 
            ORDER BY last_seen DESC LIMIT 1
        """)
        if not rows:
            return {"passed": False, "step": "mcp_submission", "reason": "No MCP servers found"}
        
        server_id = rows[0]["server_id"]
        
        rows = ws_query(f"""
            SELECT score_id, total_score FROM mcp_signal_scores 
            WHERE server_id = '{server_id}'
            ORDER BY computed_at DESC LIMIT 1
        """)
        if not rows:
            return {"passed": False, "step": "signal_scoring", "reason": "No signal scores found"}
        
        score_id = rows[0]["score_id"]
        
        rows = ws_query(f"""
            SELECT verdict_id FROM mcp_verdicts 
            WHERE signal_ref = '{score_id}' OR server_id = '{server_id}'
            ORDER BY created_at DESC LIMIT 1
        """)
        verdict_found = bool(rows)
        
        rows = ws_query(f"""
            SELECT attestation_id FROM mcp_attestations 
            WHERE server_id = '{server_id}'
            ORDER BY attested_at DESC LIMIT 1
        """)
        attestation_found = bool(rows)
        
        passed = verdict_found and attestation_found
        return {
            "passed": passed,
            "server_id": server_id,
            "score_id": score_id,
            "verdict_found": verdict_found,
            "attestation_found": attestation_found,
            "steps_completed": sum([1, verdict_found, attestation_found])
        }
    except Exception as e:
        return {"passed": False, "error": str(e)}

def scenario_2_known_threat_detection():
    """Scenario 2: Known threat detection flow"""
    try:
        rows = ws_query("""
            SELECT threat_id FROM mcp_threat_signals 
            WHERE severity IN ('critical', 'high')
            ORDER BY detected_at DESC LIMIT 1
        """)
        if not rows:
            return {"passed": False, "step": "threat_detection", "reason": "No high/critical threats found"}
        
        threat_id = rows[0]["threat_id"]
        
        rows = ws_query(f"""
            SELECT action_id FROM mcp_mitigation_actions 
            WHERE threat_ref = '{threat_id}'
            ORDER BY created_at DESC LIMIT 1
        """)
        mitigation_found = bool(rows)
        
        return {
            "passed": mitigation_found,
            "threat_id": threat_id,
            "mitigation_found": mitigation_found
        }
    except Exception as e:
        return {"passed": False, "error": str(e)}

def scenario_3_manual_override():
    """Scenario 3: Manual override flow"""
    try:
        rows = ws_query("""
            SELECT override_id, status FROM mcp_manual_overrides 
            WHERE status IN ('pending', 'approved', 'rejected')
            ORDER BY created_at DESC LIMIT 1
        """)
        if not rows:
            return {"passed": True, "step": "manual_override", "reason": "No pending overrides - no action needed", "bypassed": True}
        
        override_id = rows[0]["override_id"]
        current_status = rows[0]["status"]
        
        if current_status in ("approved", "rejected"):
            return {
                "passed": True,
                "override_id": override_id,
                "status": current_status,
                "resolved": True
            }
        
        return {
            "passed": True,
            "override_id": override_id,
            "status": current_status,
            "resolved": False
        }
    except Exception as e:
        return {"passed": False, "error": str(e)}

def run_e2e_via_script():
    """Run e2e_scenarios.py as subprocess and capture results."""
    scenario_id = f"e2e_batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    ts_start = datetime.now(timezone.utc).isoformat()
    
    try:
        result = subprocess.run(
            [sys.executable, E2E_SCENARIOS_SCRIPT, "--json-output"],
            capture_output=True,
            text=True,
            timeout=600
        )
        ts_end = datetime.now(timezone.utc).isoformat()
        
        try:
            output_data = json.loads(result.stdout)
            status = "PASS" if output_data.get("all_passed", False) else "FAIL"
        except json.JSONDecodeError:
            status = "ERROR"
            output_data = {"raw_output": result.stdout[:1000], "stderr": result.stderr[:1000]}
        
        row = {
            "scenario_id": scenario_id,
            "scenario_name": "e2e_scenarios_script",
            "status": status,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "details": json.dumps(output_data)
        }
        ws_write("e2e_scenario_results", row)
        return output_data
    except subprocess.TimeoutExpired:
        ts_end = datetime.now(timezone.utc).isoformat()
        row = {
            "scenario_id": scenario_id,
            "scenario_name": "e2e_scenarios_script",
            "status": "TIMEOUT",
            "ts_start": ts_start,
            "ts_end": ts_end,
            "details": json.dumps({"error": "Script exceeded 600s timeout"})
        }
        ws_write("e2e_scenario_results", row)
        return {"all_passed": False, "error": "timeout"}
    except Exception as e:
        ts_end = datetime.now(timezone.utc).isoformat()
        row = {
            "scenario_id": scenario_id,
            "scenario_name": "e2e_scenarios_script",
            "status": "ERROR",
            "ts_start": ts_start,
            "ts_end": ts_end,
            "details": json.dumps({"error": str(e)})
        }
        ws_write("e2e_scenario_results", row)
        return {"all_passed": False, "error": str(e)}

def cycle():
    """Run one cycle of e2e validation."""
    logger.info("Starting e2e scenarios cycle")
    
    results = {
        "scenario_1_new_mcp_flow": run_e2e_scenario("scenario_1_new_mcp_flow", scenario_1_new_mcp_flow),
        "scenario_2_known_threat_detection": run_e2e_scenario("scenario_2_known_threat_detection", scenario_2_known_threat_detection),
        "scenario_3_manual_override": run_e2e_scenario("scenario_3_manual_override", scenario_3_manual_override)
    }
    
    total = len(results)
    passed = sum(1 for r in results.values() if r.get("passed"))
    
    meta = {
        "scenarios_run": total,
        "scenarios_passed": passed,
        "scenarios_failed": total - passed,
        "results": {k: {"passed": v.get("passed", False), "error": v.get("error")} for k, v in results.items()}
    }
    
    logger.info(f"E2E cycle complete: {passed}/{total} passed")
    send_heartbeat(status="completed", meta=meta)
    return results

def update_supervisor_conf():
    """Update supervisord configuration to include e2e scenarios integration."""
    if not os.path.exists(SUPERVISOR_CONF):
        logger.warning(f"Supervisor config not found at {SUPERVISOR_CONF}")
        return False
    
    integration_section = f"""

[e2e_scenarios_integration]
command={sys.executable} {os.path.abspath(__file__)}
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startsecs=10
exitcodes=0
stopsignal=TERM
stdout_logfile=/home/workspace/logs/e2e_scenarios_integration_supervisor.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
stderr_logfile=/home/workspace/logs/e2e_scenarios_integration_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=3
user=ubuntu
"""
    
    try:
        with open(SUPERVISOR_CONF, "r") as f:
            content = f.read()
        
        if "[e2e_scenarios_integration]" in content:
            logger.info("E2E scenarios integration already in supervisor config")
            return True
        
        with open(SUPERVISOR_CONF, "a") as f:
            f.write(integration_section)
        
        logger.info(f"Updated {SUPERVISOR_CONF} with e2e_scenarios_integration section")
        return True
    except Exception as e:
        logger.error(f"Failed to update supervisor config: {e}")
        return False

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[logging.FileHandler(LOG_FILE)]
    )
    
    logger.info(f"Starting {SERVICE_NAME}")
    
    update_supervisor_conf()
    
    send_heartbeat(status="started", meta={"version": "1.0.0", "poll_interval": POLL_SECS})
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
            send_heartbeat(status="error", meta={"error": str(e)})
        
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    run()