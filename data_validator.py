import os
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from threading import Lock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVICE_NAME = "data_validator"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 300
CYCLE_INTERVAL = 21600

VERDICTS = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED', 'CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'KNOWN_THREAT', 'INSUFFICIENT']
VALID_VERDICTS_SET = set(VERDICTS)

_check_lock = Lock()

def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error(f"Query error: {e}")
        return []

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write error to {table}: {e}")
        return False

def send_heartbeat() -> bool:
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "running"
            }
        }
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")
        return False

def check_single_instance() -> bool:
    with _check_lock:
        sql = "SELECT COUNT(*) as cnt FROM service_health WHERE service = ? AND last_heartbeat > now() - interval '5 minutes'"
        results = ws_query(sql, {"p1": SERVICE_NAME})
        if results and results[0].get("cnt", 0) > 1:
            logger.warning(f"{SERVICE_NAME} instance already running, exiting")
            return False
        return True

def validate_trust_scores() -> List[Dict[str, Any]]:
    violations = []
    sql = """
        SELECT id, server_id, trust_score 
        FROM mcp_server_registry 
        WHERE trust_score IS NOT NULL 
        AND (trust_score < 0 OR trust_score > 100)
    """
    results = ws_query(sql)
    for row in results:
        violations.append({
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"trust_score {row.get('trust_score')} outside valid range [0-100] for server {row.get('server_id')}",
            "table": "mcp_server_registry",
            "record_id": row.get("id"),
            "field": "trust_score",
            "invalid_value": row.get("trust_score"),
            "severity": "MEDIUM",
            "detected_at": datetime.now(timezone.utc).isoformat()
        })
        ws_write("mesh_events", [{
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"trust_score {row.get('trust_score')} outside valid range [0-100] for server {row.get('server_id')}",
            "table": "mcp_server_registry",
            "record_id": row.get("id")
        }])
    return violations

def validate_verdicts() -> List[Dict[str, Any]]:
    violations = []
    sql = f"""
        SELECT id, server_id, verdict 
        FROM mcp_server_registry 
        WHERE verdict IS NOT NULL 
        AND verdict NOT IN ({','.join(['?' for _ in VERDICTS])})
    """
    params = list(VERDICTS)
    results = ws_query(sql)
    for row in results:
        violations.append({
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"invalid verdict '{row.get('verdict')}' not in VERDICTS list for server {row.get('server_id')}",
            "table": "mcp_server_registry",
            "record_id": row.get("id"),
            "field": "verdict",
            "invalid_value": row.get("verdict"),
            "severity": "HIGH",
            "detected_at": datetime.now(timezone.utc).isoformat()
        })
        ws_write("mesh_events", [{
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"invalid verdict '{row.get('verdict')}' not in VERDICTS list for server {row.get('server_id')}",
            "table": "mcp_server_registry",
            "record_id": row.get("id")
        }])
    return violations

def validate_required_fields() -> List[Dict[str, Any]]:
    violations = []
    sql = """
        SELECT id, server_id, name, url 
        FROM mcp_server_registry 
        WHERE server_id IS NULL 
        OR (name IS NULL OR name = '') 
        OR (url IS NULL OR url = '')
    """
    results = ws_query(sql)
    for row in results:
        missing_fields = []
        if not row.get("server_id"):
            missing_fields.append("server_id")
        if not row.get("name"):
            missing_fields.append("name")
        if not row.get("url"):
            missing_fields.append("url")
        
        violations.append({
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"missing required fields {missing_fields} for server_id {row.get('server_id')}",
            "table": "mcp_server_registry",
            "record_id": row.get("id"),
            "field": "required_fields",
            "missing_fields": missing_fields,
            "severity": "HIGH",
            "detected_at": datetime.now(timezone.utc).isoformat()
        })
        ws_write("mesh_events", [{
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"missing required fields {missing_fields} for server_id {row.get('server_id')}",
            "table": "mcp_server_registry",
            "record_id": row.get("id")
        }])
    return violations

def validate_signal_scores() -> List[Dict[str, Any]]:
    violations = []
    sql = """
        SELECT id, server_id, signal_name, score 
        FROM mcp_signal_scores 
        WHERE score IS NOT NULL 
        AND (score < 0 OR score > 100)
    """
    results = ws_query(sql)
    for row in results:
        violations.append({
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"signal score {row.get('score')} outside valid range [0-100] for server {row.get('server_id')}, signal {row.get('signal_name')}",
            "table": "mcp_signal_scores",
            "record_id": row.get("id"),
            "field": "score",
            "invalid_value": row.get("score"),
            "severity": "MEDIUM",
            "detected_at": datetime.now(timezone.utc).isoformat()
        })
        ws_write("mesh_events", [{
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"signal score {row.get('score')} outside valid range [0-100] for server {row.get('server_id')}, signal {row.get('signal_name')}",
            "table": "mcp_signal_scores",
            "record_id": row.get("id")
        }])
    return violations

def validate_attestations() -> List[Dict[str, Any]]:
    violations = []
    sql = """
        SELECT id, server_id, valid_until, status 
        FROM mcp_attestations 
        WHERE valid_until IS NOT NULL 
        AND valid_until < now() 
        AND (status IS NULL OR status != 'expired')
    """
    results = ws_query(sql)
    for row in results:
        violations.append({
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"attestation valid_until in past ({row.get('valid_until')}) but status is '{row.get('status')}' instead of 'expired' for server {row.get('server_id')}",
            "table": "mcp_attestations",
            "record_id": row.get("id"),
            "field": "status",
            "invalid_value": row.get("status"),
            "severity": "LOW",
            "detected_at": datetime.now(timezone.utc).isoformat()
        })
        ws_write("mesh_events", [{
            "agent_id": "zo_sentinel.validator",
            "action": "data_integrity_violation",
            "reason": f"attestation valid_until in past but status not 'expired' for server {row.get('server_id')}",
            "table": "mcp_attestations",
            "record_id": row.get("id")
        }])
    return violations

def run_validation_cycle() -> Dict[str, Any]:
    logger.info("Starting data validation cycle")
    all_violations = []
    
    all_violations.extend(validate_trust_scores())
    logger.info(f"Trust score violations: {len(all_violations)}")
    
    all_violations.extend(validate_verdicts())
    logger.info(f"Total violations after verdict check: {len(all_violations)}")
    
    all_violations.extend(validate_required_fields())
    logger.info(f"Total violations after required fields check: {len(all_violations)}")
    
    all_violations.extend(validate_signal_scores())
    logger.info(f"Total violations after signal scores check: {len(all_violations)}")
    
    all_violations.extend(validate_attestations())
    logger.info(f"Total violations after attestations check: {len(all_violations)}")
    
    ws_write("mesh_events", [{
        "agent_id": "zo_sentinel.validator",
        "action": "validation_complete",
        "violations_found": len(all_violations),
        "tables_checked": [
            "mcp_server_registry (trust_score, verdict, required fields)",
            "mcp_signal_scores (score)",
            "mcp_attestations (valid_until, status)"
        ],
        "completed_at": datetime.now(timezone.utc).isoformat()
    }])
    
    logger.info(f"Validation cycle complete. Total violations: {len(all_violations)}")
    return {
        "total_violations": len(all_violations),
        "violations": all_violations
    }

def run():
    logger.info(f"Starting {SERVICE_NAME} daemon")
    
    if not check_single_instance():
        logger.error("Another instance is running. Exiting.")
        return
    
    logger.info(f"{SERVICE_NAME} acquiring single-instance lock")
    
    while True:
        try:
            send_heartbeat()
            run_validation_cycle()
        except Exception as e:
            logger.error(f"Validation cycle error: {e}")
        
        logger.info(f"Sleeping for {CYCLE_INTERVAL} seconds until next validation cycle")
        time.sleep(CYCLE_INTERVAL)

if __name__ == "__main__":
    run()