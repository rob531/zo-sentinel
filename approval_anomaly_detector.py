#!/usr/bin/env python3
"""
approval_anomaly_detector.py -- ZO-SENTINEL approval workflow anomaly detector.
Detects anomalous patterns in approval decisions: rubber-stamp analysts,
speed anomalies, verdict overrides, bulk approvals, and self-approvals.
"""
import os
import sys
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("approval_anomaly_detector")

SERVICE_NAME = "zo_sentinel.approval_anomaly_detector"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8773"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 3600

PID_FILE = "/tmp/zo_sentinel_approval_anomaly_detector.pid"


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_db_path() -> str:
    return os.environ.get("SENTINEL_DB", "/tmp/zo_sentinel.duckdb")


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.warning(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            logger.info("Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Clean up PID file on exit."""
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def send_heartbeat():
    """Send heartbeat to service health table."""
    payload = {
        "table": "service_health",
        "rows": {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat() + "Z"
        }
    }
    try:
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute query via inference router."""
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json().get("rows", [])
        return []
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write to table via write service."""
    payload = {"table": table, "rows": rows}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            return True
        logger.warning(f"Write failed: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        logger.error(f"Write error: {e}")
        return False


def ensure_tables():
    """Ensure corrections and mesh_events tables exist."""
    corrections_create = """
    CREATE TABLE IF NOT EXISTS mcp_corrections (
        id BIGINT PRIMARY KEY,
        agent_id VARCHAR,
        action VARCHAR,
        reason TEXT,
        cluster VARCHAR,
        server_id VARCHAR,
        metadata TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """
    mesh_events_create = """
    CREATE TABLE IF NOT EXISTS mesh_events (
        id BIGINT PRIMARY KEY,
        event_type VARCHAR,
        severity VARCHAR,
        source VARCHAR,
        message TEXT,
        server_id VARCHAR,
        metadata TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """
    for sql in [corrections_create, mesh_events_create]:
        try:
            requests.post(EXECUTE_URL, json={"sql": sql}, timeout=10)
        except Exception as e:
            logger.warning(f"Table creation warning: {e}")


def get_recent_decisions(hours: int = 24) -> List[Dict[str, Any]]:
    """Fetch recent approval decisions."""
    sql = f"""
    SELECT 
        d.id,
        d.server_id,
        d.verdict,
        d.analyst_name,
        d.requested_by,
        d.decision_time,
        d.submission_time,
        r.trust_score,
        r.name as server_name
    FROM mcp_decisions d
    LEFT JOIN mcp_server_registry r ON d.server_id = r.server_id
    WHERE d.decision_time >= now() - INTERVAL '{hours} hours'
    ORDER BY d.decision_time DESC
    """
    return ws_query(sql)


def get_analyst_stats(decisions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Calculate per-analyst statistics."""
    stats = {}
    for d in decisions:
        analyst = d.get("analyst_name") or "unknown"
        if analyst not in stats:
            stats[analyst] = {"total": 0, "approved": 0, "denied": 0}
        stats[analyst]["total"] += 1
        if d.get("verdict") == "ALLOW":
            stats[analyst]["approved"] += 1
        elif d.get("verdict") == "DENY":
            stats[analyst]["denied"] += 1
    return stats


def detect_rubber_stamp_analysts(analyst_stats: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect analysts approving >90% of submissions."""
    anomalies = []
    for analyst, stats in analyst_stats.items():
        if stats["total"] >= 5:
            approval_rate = stats["approved"] / stats["total"]
            if approval_rate > 0.90:
                anomalies.append({
                    "anomaly_type": "rubber_stamp_analyst",
                    "analyst": analyst,
                    "approval_rate": round(approval_rate, 3),
                    "total_decisions": stats["total"],
                    "approved": stats["approved"],
                    "description": f"Analyst approving {approval_rate*100:.1f}% of submissions ({stats['total']} decisions)"
                })
    return anomalies


def detect_speed_anomalies(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect decisions made <2 minutes after submission."""
    anomalies = []
    for d in decisions:
        try:
            submission = d.get("submission_time")
            decision = d.get("decision_time")
            if submission and decision:
                if isinstance(submission, str):
                    submission = datetime.fromisoformat(submission.replace("Z", "+00:00"))
                if isinstance(decision, str):
                    decision = datetime.fromisoformat(decision.replace("Z", "+00:00"))
                delta = (decision - submission).total_seconds()
                if 0 < delta < 120:
                    anomalies.append({
                        "anomaly_type": "suspicious_speed",
                        "analyst": d.get("analyst_name"),
                        "server_id": d.get("server_id"),
                        "server_name": d.get("server_name"),
                        "decision_seconds": round(delta, 1),
                        "description": f"Decision made {delta:.0f}s after submission (threshold: 120s)"
                    })
        except Exception as e:
            logger.debug(f"Speed check error: {e}")
    return anomalies


def detect_verdict_overrides(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect approved servers with trust_score < 30."""
    anomalies = []
    for d in decisions:
        if d.get("verdict") == "ALLOW":
            trust_score = d.get("trust_score")
            if trust_score is not None and trust_score < 30:
                anomalies.append({
                    "anomaly_type": "anomalous_approval",
                    "analyst": d.get("analyst_name"),
                    "server_id": d.get("server_id"),
                    "server_name": d.get("server_name"),
                    "trust_score": trust_score,
                    "description": f"Approved server with trust_score {trust_score} (< 30 threshold)"
                })
    return anomalies


def detect_bulk_approval_bursts(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect >5 ALLOW decisions from same analyst in 1 hour window."""
    allow_decisions = [d for d in decisions if d.get("verdict") == "ALLOW"]
    anomalies = []
    
    for analyst in set(d.get("analyst_name") or "unknown" for d in allow_decisions):
        analyst_allows = sorted(
            [d for d in allow_decisions if d.get("analyst_name") == analyst],
            key=lambda x: x.get("decision_time", "")
        )
        
        for i, decision in enumerate(analyst_allows):
            try:
                window_start = decision.get("decision_time")
                if isinstance(window_start, str):
                    window_start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
                window_end = window_start + timedelta(hours=1)
                
                burst = [decision]
                for j in range(i + 1, len(analyst_allows)):
                    next_dec = analyst_allows[j]
                    if isinstance(next_dec.get("decision_time"), str):
                        next_time = datetime.fromisoformat(next_dec.get("decision_time").replace("Z", "+00:00"))
                    else:
                        next_time = next_dec.get("decision_time")
                    
                    if next_time <= window_end:
                        burst.append(next_dec)
                    else:
                        break
                
                if len(burst) > 5:
                    anomalies.append({
                        "anomaly_type": "bulk_approval_burst",
                        "analyst": analyst,
                        "burst_count": len(burst),
                        "window_start": burst[0].get("decision_time"),
                        "description": f"{len(burst)} ALLOW decisions in 1 hour window"
                    })
            except Exception as e:
                logger.debug(f"Bulk check error: {e}")
    
    return anomalies


def detect_self_approvals(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect self-approval patterns (requested_by == analyst_name)."""
    anomalies = []
    for d in decisions:
        requested_by = d.get("requested_by")
        analyst = d.get("analyst_name")
        if requested_by and analyst and requested_by == analyst:
            anomalies.append({
                "anomaly_type": "conflict_of_interest",
                "analyst": analyst,
                "server_id": d.get("server_id"),
                "server_name": d.get("server_name"),
                "description": f"Self-approval: {analyst} requested and approved this server"
            })
    return anomalies


def report_anomaly(anomaly: Dict[str, Any]) -> bool:
    """Report anomaly to corrections and mesh_events tables."""
    cluster = "governance"
    agent_id = SERVICE_NAME
    reason = anomaly.pop("description", "")
    anomaly_type = anomaly.pop("anomaly_type", "unknown")
    server_id = anomaly.pop("server_id", None)
    
    correction_row = {
        "agent_id": agent_id,
        "action": "approval_anomaly",
        "reason": f"{anomaly_type}: {reason}",
        "cluster": cluster
    }
    if server_id:
        correction_row["server_id"] = server_id
    if anomaly:
        import json
        correction_row["metadata"] = json.dumps(anomaly)
    
    mesh_row = {
        "event_type": "approval_anomaly",
        "severity": "WARNING",
        "source": agent_id,
        "message": f"{anomaly_type}: {reason}",
        "server_id": server_id
    }
    if anomaly:
        import json
        mesh_row["metadata"] = json.dumps(anomaly)
    
    corrections_ok = ws_write("mcp_corrections", correction_row)
    mesh_ok = ws_write("mesh_events", mesh_row)
    
    return corrections_ok or mesh_ok


def cycle():
    """Main detection cycle."""
    logger.info("Running approval anomaly detection cycle")
    
    try:
        decisions = get_recent_decisions(hours=24)
        logger.info(f"Retrieved {len(decisions)} recent decisions")
        
        if not decisions:
            logger.info("No decisions to analyze")
            return
        
        all_anomalies = []
        
        analyst_stats = get_analyst_stats(decisions)
        rubber_stamp_anomalies = detect_rubber_stamp_analysts(analyst_stats)
        all_anomalies.extend(rubber_stamp_anomalies)
        
        speed_anomalies = detect_speed_anomalies(decisions)
        all_anomalies.extend(speed_anomalies)
        
        verdict_overrides = detect_verdict_overrides(decisions)
        all_anomalies.extend(verdict_overrides)
        
        bulk_bursts = detect_bulk_approval_bursts(decisions)
        all_anomalies.extend(bulk_bursts)
        
        self_approvals = detect_self_approvals(decisions)
        all_anomalies.extend(self_approvals)
        
        logger.info(f"Detected {len(all_anomalies)} anomalies")
        
        for anomaly in all_anomalies:
            anomaly_type = anomaly.get("anomaly_type", "unknown")
            logger.warning(f"Anomaly detected: {anomaly_type} - {anomaly.get('description', '')}")
            report_anomaly(anomaly)
        
    except Exception as e:
        logger.error(f"Cycle error: {e}", exc_info=True)


def run():
    """Main run loop."""
    logger.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        logger.error("Cannot acquire lock. Exiting.")
        sys.exit(1)
    
    try:
        ensure_tables()
        logger.info("Tables verified")
        
        send_heartbeat()
        last_heartbeat = time.time()
        
        while True:
            cycle()
            
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = now
            
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        remove_pid_file()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    run()