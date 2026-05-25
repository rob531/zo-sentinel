from fastapi import FastAPI
import uvicorn
import time
import requests
import os
import signal
import json
from datetime import datetime, timedelta

SERVICE_NAME = "trust_synthesiser_v3"
PORT = 8787
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
    "dependency_signal": 0.07,
    "injection_resilience": 0.12
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
            return False
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    global running
    running = False
    remove_pid_file()

def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return {"rows": [], "count": 0}
    except Exception as e:
        print(f"[ERROR] Query failed: {e}")
        return {"rows": [], "count": 0}

def ws_write(table, rows):
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows}, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Write failed: {e}")
        return False

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Execute failed: {e}")
        return False

def send_heartbeat():
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": datetime.now().isoformat()}])
    except Exception:
        pass

def ensure_tables():
    ws_execute("""
        CREATE TABLE IF NOT EXISTS trust_synthesiser_v3_runs (
            run_id INTEGER PRIMARY KEY,
            executed_at TIMESTAMP,
            servers_processed INTEGER,
            servers_updated INTEGER,
            duration_ms INTEGER
        )
    """)
    ws_execute("""
        CREATE TABLE IF NOT EXISTS trust_synthesis_log (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            computed_score DOUBLE,
            verdict VARCHAR,
            confidence VARCHAR,
            dimensions_loaded VARCHAR,
            weights_applied VARCHAR,
            synthesis_timestamp TIMESTAMP
        )
    """)

def load_signal_weights():
    global _signal_weights, _last_weight_refresh
    result = ws_query("""
        SELECT signal_name, score as weight
        FROM signal_weights
        WHERE is_active = TRUE
        AND (valid_until IS NULL OR valid_until > NOW())
        ORDER BY signal_name
    """)
    if result and result.get("rows") and len(result["rows"]) > 0:
        for row in result["rows"]:
            sn = row.get("signal_name", "")
            wt = row.get("weight")
            if sn and wt is not None:
                _signal_weights[sn] = float(wt)
        _last_weight_refresh = time.time()
        print(f"[INFO] Loaded {len(result['rows'])} signal weights from DB")
    else:
        print("[WARN] No active weights in DB, using defaults")

def normalize_weight_key(key):
    key = key.lower().strip()
    replacements = {
        "attestationsignal": "attestation_signal",
        "scansignal": "scan_signal",
        "threatsignal": "threat_signal",
        "behaviorsignal": "behavior_signal",
        "reputationsignal": "reputation_signal",
        "communitysignal": "community_signal",
        "agesignal": "age_signal",
        "dependencysignal": "dependency_signal",
        "injectionresilience": "injection_resilience",
        "injection_resilience_signal": "injection_resilience"
    }
    return replacements.get(key, key)

def query_signal_scores(server_id):
    result = ws_query(f"""
        SELECT signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        ORDER BY scored_at DESC
    """)
    return result.get("rows", []) if result else []

def compute_composite_score(signal_rows, weights):
    if not signal_rows:
        return 0.0, {}
    
    dimension_scores = {}
    raw_scores = {}
    
    for row in signal_rows:
        sn = row.get("signal_name", "")
        sn_normalized = normalize_weight_key(sn)
        
        score = row.get("score", 0)
        if score is None:
            score = 0
        
        if sn_normalized not in dimension_scores:
            dimension_scores[sn_normalized] = []
        dimension_scores[sn_normalized].append(float(score))
    
    for dim in dimension_scores:
        scores = dimension_scores[dim]
        raw_scores[dim] = sum(scores) / len(scores) if scores else 0.0
    
    weighted_sum = 0.0
    weight_total = 0.0
    
    for dim, score in raw_scores.items():
        weight = weights.get(dim, 0.0)
        if weight > 0:
            weighted_sum += score * weight
            weight_total += weight
    
    if weight_total > 0:
        composite = weighted_sum / weight_total
    else:
        composite = 0.0
    
    return float(composite), raw_scores

def compute_confidence(dimension_scores, dim_count):
    if dim_count == 0:
        return "low"
    
    non_zero_dims = sum(1 for s in dimension_scores.values() if s > 0)
    coverage = non_zero_dims / dim_count
    
    score_variance = 0
    if dimension_scores:
        scores = list(dimension_scores.values())
        if scores:
            mean = sum(scores) / len(scores)
            score_variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    
    confidence_map = _confidence_thresholds.copy()
    
    if coverage >= 0.8 and score_variance < 0.1:
        return "high"
    elif coverage >= 0.5 or score_variance < 0.2:
        return "medium"
    else:
        return "low"

def compute_verdict(score):
    thresholds = VERDICT_THRESHOLDS.copy()
    if score >= thresholds["TRUSTED"]:
        return "TRUSTED"
    elif score >= thresholds["LIKELY_TRUSTED"]:
        return "LIKELY_TRUSTED"
    elif score >= thresholds["NEEDS_REVIEW"]:
        return "NEEDS_REVIEW"
    elif score >= thresholds["UNTRUSTED"]:
        return "UNTRUSTED"
    else:
        return "MALICIOUS"

def synthesize_trust(server_id, server_name):
    signal_rows = query_signal_scores(server_id)
    
    composite_score, dimension_scores = compute_composite_score(signal_rows, _signal_weights)
    
    dim_count = len(_signal_weights)
    confidence = compute_confidence(dimension_scores, dim_count)
    
    verdict = compute_verdict(composite_score)
    
    dimensions_loaded = json.dumps(list(dimension_scores.keys()))
    weights_applied = json.dumps({k: _signal_weights.get(k, 0) for k in dimension_scores.keys()})
    
    return {
        "server_id": server_id,
        "server_name": server_name,
        "composite_score": composite_score,
        "verdict": verdict,
        "confidence": confidence,
        "dimension_scores": dimension_scores,
        "dimensions_loaded": dimensions_loaded,
        "weights_applied": weights_applied
    }

def update_registry(server_id, score, verdict, confidence):
    ws_execute(f"""
        UPDATE mcp_server_registry
        SET trust_score = {score},
            verdict = '{verdict}',
            last_synthesized = NOW()
        WHERE server_id = '{server_id}'
    """)

def log_synthesis(server_id, result, timestamp):
    log_entry = {
        "server_id": server_id,
        "computed_score": result["composite_score"],
        "verdict": result["verdict"],
        "confidence": result["confidence"],
        "dimensions_loaded": result["dimensions_loaded"],
        "weights_applied": result["weights_applied"],
        "synthesis_timestamp": timestamp
    }
    ws_write("trust_synthesis_log", [log_entry])

def run_cycle():
    global _last_weight_refresh
    
    if _last_weight_refresh is None or (time.time() - _last_weight_refresh) > _weight_refresh_interval:
        load_signal_weights()
    
    start_ts = datetime.now()
    
    result = ws_query("""
        SELECT server_id, name
        FROM mcp_server_registry
        WHERE scan_count > 0
        AND (verdict IS NULL OR verdict = '' OR last_synthesized IS NULL OR last_synthesized < NOW() - INTERVAL '1 hour')
        ORDER BY scan_count DESC, risk_rank DESC
        LIMIT 50
    """)
    
    servers = result.get("rows", []) if result else []
    servers_updated = 0
    
    for server in servers:
        server_id = server.get("server_id", "")
        server_name = server.get("name", "")
        
        if not server_id:
            continue
        
        try:
            synthesis = synthesize_trust(server_id, server_name)
            
            update_registry(server_id, synthesis["composite_score"], synthesis["verdict"], synthesis["confidence"])
            log_synthesis(server_id, synthesis, start_ts.isoformat())
            
            servers_updated += 1
            print(f"[OK] {server_name}: score={synthesis['composite_score']:.3f} verdict={synthesis['verdict']} confidence={synthesis['confidence']}")
        except Exception as e:
            print(f"[ERROR] Failed synthesis for {server_name}: {e}")
    
    end_ts = datetime.now()
    duration_ms = int((end_ts - start_ts).total_seconds() * 1000)
    
    ws_write("trust_synthesiser_v3_runs", [{
        "executed_at": start_ts.isoformat(),
        "servers_processed": len(servers),
        "servers_updated": servers_updated,
        "duration_ms": duration_ms
    }])
    
    print(f"[CYCLE] Processed {len(servers)} servers, updated {servers_updated} in {duration_ms}ms")

def run():
    if not check_single_instance():
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    load_signal_weights()
    
    print(f"[START] {SERVICE_NAME} running on port {PORT}")
    print(f"[INFO] PID file: {PID_FILE}")
    print(f"[INFO] Using weights: {_signal_weights}")
    
    cycle_count = 0
    last_heartbeat = time.time()
    
    while running:
        try:
            run_cycle()
            cycle_count += 1
            
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = time.time()
            
            time.sleep(POLL_SECS)
        except Exception as e:
            print(f"[ERROR] Cycle failed: {e}")
            time.sleep(10)
    
    remove_pid_file()
    print(f"[STOP] {SERVICE_NAME} stopped after {cycle_count} cycles")

@app.get("/health")
def health():
    uptime = int(time.time() - start_time)
    return {"status": "ok", "service": SERVICE_NAME, "uptime": uptime}

@app.get("/synthesize/{server_id}")
def synthesize(server_id: str):
    result = ws_query(f"SELECT name FROM mcp_server_registry WHERE server_id = '{server_id}'")
    rows = result.get("rows", []) if result else []
    if not rows:
        return {"error": "Server not found"}
    
    server_name = rows[0].get("name", "")
    synthesis = synthesize_trust(server_id, server_name)
    return synthesis

@app.get("/scores")
def get_scores():
    result = ws_query("""
        SELECT server_id, trust_score, verdict, confidence
        FROM mcp_server_registry
        WHERE trust_score IS NOT NULL
        ORDER BY trust_score DESC
        LIMIT 100
    """)
    return {"scores": result.get("rows", [])}

@app.get("/weights")
def get_weights():
    return {"weights": _signal_weights, "refreshed_at": _last_weight_refresh}

if __name__ == "__main__":
    run()