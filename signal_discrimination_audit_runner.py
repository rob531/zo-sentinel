import time
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import signal
import requests

SERVICE_NAME = "signal_discrimination_audit_runner"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"

POLL_SECS = 14400
HEARTBEAT_INTERVAL = 300
DISTINCT_SCORE_THRESHOLD = 10

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


def check_single_instance() -> bool:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            import os
            os.kill(old_pid, 0)
            logger.error(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            logger.info(f"Stale PID file found, removing")
    pid_path.write_text(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        Path(PID_FILE).unlink()
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    exit(0)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []


def send_heartbeat() -> None:
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            }
        }
        requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def query_signal_scores() -> List[Dict[str, Any]]:
    sql = "SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores"
    return ws_query(sql)


def query_signal_enrichments() -> List[Dict[str, Any]]:
    sql = "SELECT server_id, signal_type, score, evidence, computed_at FROM mcp_signal_enrichments"
    return ws_query(sql)


def compute_audit_results(signal_scores: List[Dict[str, Any]], enrichments: List[Dict[str, Any]]) -> Dict[str, Any]:
    audit_time = datetime.utcnow().isoformat()
    
    all_scores = []
    
    for row in signal_scores:
        all_scores.append({
            "source": "mcp_signal_scores",
            "signal_type": row.get("signal_name"),
            "server_id": row.get("server_id"),
            "score": row.get("score"),
            "evidence": row.get("evidence"),
            "scored_at": row.get("scored_at")
        })
    
    for row in enrichments:
        all_scores.append({
            "source": "mcp_signal_enrichments",
            "signal_type": row.get("signal_type"),
            "server_id": row.get("server_id"),
            "score": row.get("score"),
            "evidence": row.get("evidence"),
            "computed_at": row.get("computed_at")
        })
    
    distinct_by_signal = {}
    for item in all_scores:
        sig_type = item["signal_type"]
        if sig_type not in distinct_by_signal:
            distinct_by_signal[sig_type] = {"scores": set(), "count": 0, "source": item["source"]}
        if item["score"] is not None:
            distinct_by_signal[sig_type]["scores"].add(item["score"])
            distinct_by_signal[sig_type]["count"] += 1
    
    signal_analysis = []
    weak_signals = []
    good_signals = []
    
    for sig_type, data in distinct_by_signal.items():
        distinct_count = len(data["scores"])
        min_score = min(data["scores"]) if data["scores"] else None
        max_score = max(data["scores"]) if data["scores"] else None
        score_range = max_score - min_score if min_score is not None and max_score is not None else 0
        
        signal_info = {
            "signal_type": sig_type,
            "source": data["source"],
            "distinct_score_count": distinct_count,
            "total_occurrences": data["count"],
            "min_score": min_score,
            "max_score": max_score,
            "score_range": score_range,
            "is_weak": distinct_count < DISTINCT_SCORE_THRESHOLD,
            "verdict": "WEAK" if distinct_count < DISTINCT_SCORE_THRESHOLD else "OK"
        }
        
        signal_analysis.append(signal_info)
        
        if distinct_count < DISTINCT_SCORE_THRESHOLD:
            weak_signals.append(signal_info)
        else:
            good_signals.append(signal_info)
    
    audit_result = {
        "audit_timestamp": audit_time,
        "service": SERVICE_NAME,
        "total_signals_analyzed": len(all_scores),
        "unique_signal_types": len(distinct_by_signal),
        "weak_signals_count": len(weak_signals),
        "good_signals_count": len(good_signals),
        "distinct_score_threshold": DISTINCT_SCORE_THRESHOLD,
        "weak_signals": weak_signals,
        "good_signals": good_signals,
        "all_signal_analysis": signal_analysis,
        "summary": {
            "status": "FAIL" if weak_signals else "PASS",
            "message": f"Found {len(weak_signals)} weak signal(s) with <{DISTINCT_SCORE_THRESHOLD} distinct scores"
        }
    }
    
    return audit_result


def run_audit() -> Dict[str, Any]:
    logger.info("Starting signal discrimination audit")
    
    signal_scores = query_signal_scores()
    logger.info(f"Retrieved {len(signal_scores)} signal score records")
    
    enrichments = query_signal_enrichments()
    logger.info(f"Retrieved {len(enrichments)} signal enrichment records")
    
    audit_result = compute_audit_results(signal_scores, enrichments)
    
    audit_json = json.dumps(audit_result, indent=2)
    logger.info(f"Audit results:\n{audit_json}")
    
    with open(f"/tmp/{SERVICE_NAME}_audit.json", "w") as f:
        f.write(audit_json)
    logger.info(f"Audit results written to /tmp/{SERVICE_NAME}_audit.json")
    
    if audit_result["weak_signals"]:
        logger.warning(f"SIGNAL DISCRIMINATION FAILURE: {len(audit_result['weak_signals'])} weak signals detected")
        for ws in audit_result["weak_signals"]:
            logger.warning(f"  - {ws['signal_type']}: {ws['distinct_score_count']} distinct scores, range [{ws['min_score']}-{ws['max_score']}]")
    else:
        logger.info("SIGNAL DISCRIMINATION PASS: All signals meet distinct score threshold")
    
    return audit_result


def cycle() -> None:
    last_heartbeat = time.time()
    
    while True:
        try:
            run_audit()
        except Exception as e:
            logger.error(f"Audit cycle failed: {e}")
        
        send_heartbeat()
        last_heartbeat = time.time()
        
        logger.info(f"Sleeping for {POLL_SECS} seconds until next audit")
        time.sleep(POLL_SECS)


def run() -> None:
    if not check_single_instance():
        logger.error("Cannot start: another instance is running")
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"Starting {SERVICE_NAME}")
    
    try:
        cycle()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        remove_pid_file()
        logger.info(f"{SERVICE_NAME} stopped")


if __name__ == '__main__':
    run()