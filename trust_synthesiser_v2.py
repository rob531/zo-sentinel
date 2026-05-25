from fastapi import FastAPI
import uvicorn
import time
import requests
import os
import signal
import json
from datetime import datetime, timedelta

SERVICE_NAME = "trust_synthesiser_v2"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 120
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

app = FastAPI()
start_time = time.time()
running = True

DEFAULT_SIGNAL_WEIGHTS = {
    "attestation_signal": 0.15,
    "scan_signal": 0.12,
    "threat_signal": 0.18,
    "behavior_signal": 0.14,
    "reputation_signal": 0.16,
    "community_signal": 0.10,
    "age_signal": 0.08,
    "dependency_signal": 0.07
}

DEFAULT_CONFIDENCE_THRESHOLDS = {
    "high_confidence": 0.85,
    "medium_confidence": 0.60,
    "low_confidence": 0.40
}

VERDICT_THRESHOLDS = {
    "TRUSTED": 0.80,
    "LIKELY_TRUSTED": 0.60,
    "NEEDS_REVIEW": 0.40,
    "UNTRUSTED": 0.20,
    "MALICIOUS": 0.00
}

_signal_weights = DEFAULT_SIGNAL_WEIGHTS.copy()
_confidence_thresholds = DEFAULT_CONFIDENCE_THRESHOLDS.copy()
_last_weight_refresh = None
_weight_refresh_interval = 300

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"[FATAL] {SERVICE_NAME} already running with PID {old_pid}")
            exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    global running
    running = False
    remove_pid_file()

def get_write_url():
    return WRITE_SERVICE_URL

def get_execute_url():
    return EXECUTE_URL

def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Query failed: {e}")
        return {"rows": [], "count": 0}

def ws_write(table, rows_data):
    try:
        payload = {"table": table, "rows": rows_data, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Write failed: {e}")
        return {"ok": False, "error": str(e)}

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Execute failed: {e}")
        return {"ok": False, "error": str(e)}

def send_heartbeat():
    try:
        ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": datetime.now().isoformat()})
    except Exception as e:
        print(f"[WARN] Heartbeat failed: {e}")

def load_dynamic_weights():
    global _signal_weights, _confidence_thresholds, _last_weight_refresh
    now = datetime.now()
    if _last_weight_refresh and (now - _last_weight_refresh).total_seconds() < _weight_refresh_interval:
        return
    try:
        result = ws_query("SELECT config_key, config_value FROM mcp_policy_rules WHERE config_type = 'signal_weight' AND active = true")
        rows = result.get("rows", [])
        if rows:
            for row in rows:
                key = row.get("config_key", "")
                value = row.get("config_value", "")
                if key in _signal_weights:
                    try:
                        _signal_weights[key] = float(value)
                    except (ValueError, TypeError):
                        pass
            print(f"[INFO] Loaded {len(rows)} dynamic signal weights")
        conf_result = ws_query("SELECT config_key, config_value FROM mcp_policy_rules WHERE config_type = 'confidence_threshold' AND active = true")
        conf_rows = conf_result.get("rows", [])
        if conf_rows:
            for row in conf_rows:
                key = row.get("config_key", "")
                value = row.get("config_value", "")
                if key in _confidence_thresholds:
                    try:
                        _confidence_thresholds[key] = float(value)
                    except (ValueError, TypeError):
                        pass
            print(f"[INFO] Loaded {len(conf_rows)} confidence thresholds")
        _last_weight_refresh = now
    except Exception as e:
        print(f"[WARN] Failed to load dynamic weights: {e}")

def ensure_mesh_events_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mesh_events (
        event_id INTEGER,
        event_type TEXT,
        server_id TEXT,
        payload TEXT,
        severity TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)

def emit_verdict_changed_event(server_id, old_verdict, new_verdict, confidence, signals):
    payload = json.dumps({
        "old_verdict": old_verdict,
        "new_verdict": new_verdict,
        "confidence": confidence,
        "signals": signals,
        "weights_used": _signal_weights.copy()
    })
    event_data = {
        "event_type": "VERDICT_CHANGED",
        "server_id": server_id,
        "payload": payload,
        "severity": "INFO" if new_verdict not in ["UNTRUSTED", "MALICIOUS"] else "HIGH",
        "created_at": datetime.now().isoformat()
    }
    ws_write("mesh_events", event_data)

def compute_trust_score(server_id):
    signals_result = ws_query(f"""
        SELECT signal_name, score, evidence 
        FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' 
        AND scored_at > (CURRENT_TIMESTAMP - INTERVAL '7 days')
    """)
    rows = signals_result.get("rows", [])
    if not rows:
        return None, 0.0
    weighted_sum = 0.0
    weight_sum = 0.0
    signals_used = {}
    for row in rows:
        signal_name = row.get("signal_name", "")
        score = row.get("score", 0.0)
        if signal_name in _signal_weights:
            weight = _signal_weights[signal_name]
            weighted_sum += score * weight
            weight_sum += weight
            signals_used[signal_name] = {"score": score, "weight": weight}
    if weight_sum > 0:
        trust_score = weighted_sum / weight_sum
    else:
        trust_score = 0.0
    confidence = min(1.0, len(signals_used) / 5.0)
    return trust_score, confidence, signals_used

def score_to_verdict(score):
    if score is None:
        return "NO_DATA"
    if score >= VERDICT_THRESHOLDS["TRUSTED"]:
        return "TRUSTED"
    elif score >= VERDICT_THRESHOLDS["LIKELY_TRUSTED"]:
        return "LIKELY_TRUSTED"
    elif score >= VERDICT_THRESHOLDS["NEEDS_REVIEW"]:
        return "NEEDS_REVIEW"
    elif score >= VERDICT_THRESHOLDS["UNTRUSTED"]:
        return "UNTRUSTED"
    else:
        return "MALICIOUS"

def get_servers_needing_synthesis():
    result = ws_query("""
        SELECT server_id, name, verdict 
        FROM mcp_server_registry 
        WHERE last_synthesized IS NULL 
           OR last_synthesized < CURRENT_TIMESTAMP - INTERVAL '1 hour'
        LIMIT 50
    """)
    return result.get("rows", [])

def update_server_verdict(server_id, verdict, trust_score, confidence):
    sql = f"""
        UPDATE mcp_server_registry 
        SET verdict = '{verdict}',
            trust_score = {trust_score},
            confidence = {confidence},
            last_synthesized = CURRENT_TIMESTAMP
        WHERE server_id = '{server_id}'
    """
    ws_execute(sql)

def adjust_weight(signal_name, new_weight):
    global _signal_weights
    if signal_name in _signal_weights:
        old_weight = _signal_weights[signal_name]
        _signal_weights[signal_name] = max(0.0, min(1.0, new_weight))
        ws_execute(f"""
            INSERT INTO mcp_policy_rules (config_type, config_key, config_value, active, updated_at)
            VALUES ('signal_weight', '{signal_name}', '{new_weight}', true, CURRENT_TIMESTAMP)
        """)
        return {"signal": signal_name, "old": old_weight, "new": _signal_weights[signal_name]}
    return None

def run():
    global running
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    print(f"[START] {SERVICE_NAME} initializing...")
    ensure_mesh_events_table()
    print(f"[START] {SERVICE_NAME} running on port {PORT}")
    while running:
        try:
            send_heartbeat()
            load_dynamic_weights()
            servers = get_servers_needing_synthesis()
            for server in servers:
                server_id = server.get("server_id")
                old_verdict = server.get("verdict", "NO_DATA")
                if server_id:
                    trust_score, confidence, signals_used = compute_trust_score(server_id)
                    if trust_score is not None:
                        new_verdict = score_to_verdict(trust_score)
                        update_server_verdict(server_id, new_verdict, trust_score, confidence)
                        if old_verdict != new_verdict and old_verdict != "NO_DATA":
                            emit_verdict_changed_event(server_id, old_verdict, new_verdict, confidence, signals_used)
                            print(f"[EVENT] {server_id}: {old_verdict} -> {new_verdict} (score={trust_score:.3f})")
            time.sleep(POLL_SECS)
        except Exception as e:
            print(f"[ERROR] Cycle failed: {e}")
            time.sleep(30)
    remove_pid_file()
    print(f"[STOP] {SERVICE_NAME} stopped")

@app.get("/health")
def health():
    uptime = int(time.time() - start_time)
    return {"status": "ok", "service": SERVICE_NAME, "uptime": uptime}

@app.get("/weights")
def get_weights():
    load_dynamic_weights()
    return {
        "signal_weights": _signal_weights,
        "confidence_thresholds": _confidence_thresholds,
        "last_refresh": _last_weight_refresh.isoformat() if _last_weight_refresh else None
    }

@app.post("/weights/{signal_name}")
def set_weight(signal_name: str, weight: float):
    result = adjust_weight(signal_name, weight)
    if result:
        return {"ok": True, "result": result}
    return {"ok": False, "error": f"Unknown signal: {signal_name}"}

@app.get("/synthesize/{server_id}")
def synthesize_single(server_id: str):
    trust_score, confidence, signals_used = compute_trust_score(server_id)
    if trust_score is None:
        return {"ok": False, "error": "No signals found"}
    verdict = score_to_verdict(trust_score)
    update_server_verdict(server_id, verdict, trust_score, confidence)
    return {
        "ok": True,
        "server_id": server_id,
        "trust_score": trust_score,
        "confidence": confidence,
        "verdict": verdict,
        "signals_used": signals_used
    }

if __name__ == "__main__":
    run()