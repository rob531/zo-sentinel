#!/usr/bin/env python3
"""
risk_ranker.py -- ZO-SENTINEL risk ranking daemon.
Reads mcp_server_registry, mcp_signal_scores, mcp_threat_associations.
Computes risk_rank 0-100 and writes to mcp_risk_register.
"""
import logging
import os
import sys
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import fcntl

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/home/workspace/zo_sentinel/logs/risk_ranker.log')
    ]
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 14400
SERVICE_NAME = "risk_ranker"
POLL_INTERVAL = 14400


def check_single_instance():
    """Acquire exclusive flock on /tmp/risk_ranker.lock. Exit on collision.

    Replaces the previous pgrep-based check which produced false positives
    whenever ANY other process had the script name in its command line
    (tail -f on the log, editors, grep, etc.). The flock is kernel-enforced
    and released automatically on process exit -- no stale PID files.
    Returned lock-file fd is kept alive by module-level reference.
    """
    lock_path = '/tmp/risk_ranker.lock'
    try:
        fd = open(lock_path, 'w')
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(__import__("os").getpid()))
        fd.flush()
        globals()['_single_instance_lock_fd'] = fd
        return True
    except (IOError, OSError):
        # Another instance holds the lock -- exit immediately.
        print(f"[risk_ranker] Another instance holds lock at {lock_path} -- exiting", flush=True)
        sys.exit(0)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_db_path() -> str:
    return "/home/workspace/zo_sentinel/data/zo_sentinel.db"


def send_heartbeat():
    """Send heartbeat to service_health."""
    try:
        requests.post(get_write_url(), json={
            'table': 'service_health',
            'rows': {
                'service': SERVICE_NAME,
                'last_heartbeat': datetime.now(timezone.utc).isoformat()
            },
            'wait': True
        }, timeout=5)
        log.debug("Heartbeat sent")
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute SELECT via /query endpoint (not /execute)."""
    try:
        response = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        if response.status_code == 200:
            result = response.json()
            if 'result' in result and isinstance(result['result'], list):
                return result['result']
            elif 'rows' in result:
                return result['rows']
            return result.get('data', [])
        else:
            log.error(f"Query failed: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        log.error(f"Query exception: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write to DuckDB via write_service."""
    try:
        payload = {
            'table': table,
            'rows': rows,
            'wait': True
        }
        response = requests.post(get_write_url(), json=payload, timeout=30)
        if response.status_code == 200:
            log.debug(f"Write to {table} succeeded")
            return True
        else:
            log.error(f"Write to {table} failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        log.error(f"Write exception for {table}: {e}")
        return False


def create_table() -> bool:
    """Create mcp_risk_register table if not exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_risk_register (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        name        VARCHAR,
        risk_rank   REAL,
        risk_tier   VARCHAR,
        threat_count INTEGER,
        staleness_days INTEGER,
        computed_at TIMESTAMPTZ DEFAULT now()
    )
    """
    try:
        response = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        if response.status_code == 200:
            log.info("mcp_risk_register table ready")
            return True
        else:
            log.error(f"Table creation failed: {response.status_code}")
            return False
    except Exception as e:
        log.error(f"Table creation exception: {e}")
        return False


def get_servers() -> List[Dict[str, Any]]:
    """Fetch all servers from registry with their trust scores."""
    sql = """
    SELECT 
        sr.server_id,
        sr.name,
        sr.trust_score,
        sr.last_assessed,
        sr.first_seen,
        COALESCE(
            (SELECT SUM(ABS(ss.score)) 
             FROM mcp_signal_scores ss 
             WHERE ss.server_id = sr.server_id 
             AND ss.signal_name LIKE '%permission%'), 
            0
        ) as permission_scope_raw
    FROM mcp_server_registry sr
    ORDER BY sr.server_id
    """
    return ws_query(sql)


def get_threat_counts() -> Dict[str, int]:
    """Get threat count per server."""
    sql = """
    SELECT server_id, COUNT(*) as threat_count
    FROM mcp_threat_associations
    GROUP BY server_id
    """
    results = ws_query(sql)
    return {r['server_id']: r.get('threat_count', 0) for r in results}


def compute_staleness_penalty(last_assessed: Optional[str], first_seen: Optional[str]) -> float:
    """Compute staleness penalty based on days since last assessment or first seen."""
    if not last_assessed:
        last_assessed = first_seen
    
    if not last_assessed:
        return 30.0
    
    try:
        if isinstance(last_assessed, str):
            last_dt = datetime.fromisoformat(last_assessed.replace('Z', '+00:00'))
        else:
            last_dt = last_assessed
        
        now = datetime.now(timezone.utc)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        
        days_old = (now - last_dt).total_seconds() / 86400
        
        if days_old <= 1:
            return 0.0
        elif days_old <= 7:
            return 5.0
        elif days_old <= 30:
            return 15.0
        elif days_old <= 90:
            return 25.0
        else:
            return 30.0
    except Exception as e:
        log.warning(f"Staleness calculation error: {e}")
        return 15.0


def compute_risk_rank(
    trust_score: Optional[float],
    threat_count: int,
    staleness_days: int,
    permission_scope_raw: float
) -> float:
    """
    Compute risk_rank 0-100:
    (100-trust_score)*0.4 + threat_count*10*0.3 + staleness_penalty*0.2 + permission_scope_raw*0.1
    Clamped to 0-100.
    """
    ts = trust_score if trust_score is not None else 0.0
    
    trust_component = (100 - ts) * 0.4
    threat_component = min(threat_count * 10, 100) * 0.3
    staleness_penalty = min(staleness_days * 5, 100) * 0.2
    permission_component = min(permission_scope_raw * 0.5, 100) * 0.1
    
    risk_rank = trust_component + threat_component + staleness_penalty + permission_component
    
    risk_rank = max(0.0, min(100.0, risk_rank))
    
    return round(risk_rank, 2)


def get_risk_tier(risk_rank: float) -> str:
    """Map risk_rank to risk_tier."""
    if risk_rank >= 80:
        return "CRITICAL"
    elif risk_rank >= 60:
        return "HIGH"
    elif risk_rank >= 40:
        return "MEDIUM"
    else:
        return "LOW"


def compute_staleness_days(last_assessed: Optional[str], first_seen: Optional[str]) -> int:
    """Return staleness in days."""
    if not last_assessed:
        last_assessed = first_seen
    
    if not last_assessed:
        return 365
    
    try:
        if isinstance(last_assessed, str):
            last_dt = datetime.fromisoformat(last_assessed.replace('Z', '+00:00'))
        else:
            last_dt = last_assessed
        
        now = datetime.now(timezone.utc)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        
        return int((now - last_dt).total_seconds() / 86400)
    except Exception:
        return 365


def insert_into_table(records: List[Dict[str, Any]]) -> bool:
    """Insert risk records into mcp_risk_register."""
    if not records:
        log.info("No records to insert")
        return True
    
    for record in records:
        record['computed_at'] = datetime.now(timezone.utc).isoformat()
    
    try:
        payload = {
            'table': 'mcp_risk_register',
            'rows': records,
            'wait': True
        }
        response = requests.post(get_write_url(), json=payload, timeout=30)
        if response.status_code == 200:
            log.info(f"Inserted {len(records)} risk records")
            return True
        else:
            log.error(f"Insert failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        log.error(f"Insert exception: {e}")
        return False


def clear_risk_register() -> bool:
    """Clear existing risk register before recalculation."""
    sql = "DELETE FROM mcp_risk_register"
    try:
        response = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        if response.status_code == 200:
            log.info("Cleared mcp_risk_register")
            return True
        else:
            log.warning(f"Clear failed: {response.status_code}")
            return False
    except Exception as e:
        log.error(f"Clear exception: {e}")
        return False


def generate_risk_register_md(servers: List[Dict[str, Any]], threat_counts: Dict[str, int]) -> str:
    """Generate RISK_REGISTER.md sorted by risk_rank desc."""
    rows = []
    rows.append("# ZO-SENTINEL Risk Register\n")
    rows.append(f"*Generated: {datetime.now(timezone.utc).isoformat()}*\n\n")
    rows.append("| Rank | Server ID | Name | Risk Score | Risk Tier | Threats | Staleness (days) |\n")
    rows.append("|------|-----------|------|------------|-----------|---------|-------------------|\n")
    
    for s in servers:
        server_id = s.get('server_id', 'unknown')
        name = s.get('name', 'N/A')
        trust_score = s.get('trust_score')
        threat_count = threat_counts.get(server_id, 0)
        staleness_days = compute_staleness_days(
            s.get('last_assessed'),
            s.get('first_seen')
        )
        staleness_penalty = compute_staleness_penalty(
            s.get('last_assessed'),
            s.get('first_seen')
        )
        permission_scope_raw = s.get('permission_scope_raw', 0)
        
        risk_rank = compute_risk_rank(
            trust_score,
            threat_count,
            staleness_days,
            permission_scope_raw
        )
        risk_tier = get_risk_tier(risk_rank)
        
        rows.append(f"| {risk_rank:.1f} | `{server_id}` | {name} | {trust_score or 'N/A':>6} | {risk_tier} | {threat_count} | {staleness_days} |\n")
    
    rows.append("\n## Risk Tier Definitions\n")
    rows.append("- **CRITICAL (≥80)**: Immediate attention required\n")
    rows.append("- **HIGH (≥60)**: Significant risk, review soon\n")
    rows.append("- **MEDIUM (≥40)**: Moderate risk, monitor periodically\n")
    rows.append("- **LOW (<40)**: Low risk, standard monitoring\n")
    
    return ''.join(rows)


def write_risk_register_md(content: str) -> bool:
    """Write RISK_REGISTER.md to disk."""
    output_path = Path('/home/workspace/zo_sentinel/docs/RISK_REGISTER.md')
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        log.info(f"Wrote {output_path}")
        return True
    except Exception as e:
        log.error(f"Failed to write RISK_REGISTER.md: {e}")
        return False


def cycle() -> int:
    """Execute one risk ranking cycle. Returns count of servers processed."""
    log.info("Starting risk ranking cycle")
    send_heartbeat()
    
    servers = get_servers()
    if not servers:
        log.warning("No servers found in registry")
        send_heartbeat()
        return 0
    
    log.info(f"Found {len(servers)} servers to rank")
    threat_counts = get_threat_counts()
    
    records = []
    for s in servers:
        server_id = s.get('server_id', 'unknown')
        name = s.get('name', 'N/A')
        trust_score = s.get('trust_score')
        threat_count = threat_counts.get(server_id, 0)
        
        staleness_days = compute_staleness_days(
            s.get('last_assessed'),
            s.get('first_seen')
        )
        staleness_penalty = compute_staleness_penalty(
            s.get('last_assessed'),
            s.get('first_seen')
        )
        permission_scope_raw = s.get('permission_scope_raw', 0)
        
        risk_rank = compute_risk_rank(
            trust_score,
            threat_count,
            staleness_days,
            permission_scope_raw
        )
        risk_tier = get_risk_tier(risk_rank)
        
        records.append({
            'server_id': server_id,
            'name': name,
            'risk_rank': risk_rank,
            'risk_tier': risk_tier,
            'threat_count': threat_count,
            'staleness_days': staleness_days
        })
    
    clear_risk_register()
    
    if records:
        insert_into_table(records)
    
    records.sort(key=lambda x: x['risk_rank'], reverse=True)
    md_content = generate_risk_register_md(servers, threat_counts)
    write_risk_register_md(md_content)
    
    send_heartbeat()
    log.info(f"Risk ranking cycle complete: {len(records)} servers processed")
    
    return len(records)


def run():
    """Main daemon loop."""
    log.info(f"Starting {SERVICE_NAME} daemon")
    
    os.makedirs('/home/workspace/zo_sentinel/logs', exist_ok=True)
    os.makedirs('/home/workspace/zo_sentinel/data', exist_ok=True)
    os.makedirs('/home/workspace/zo_sentinel/docs', exist_ok=True)
    
    check_single_instance()
    
    create_table()
    
    cycle()
    
    while True:
        try:
            time.sleep(POLL_INTERVAL)
            cycle()
        except KeyboardInterrupt:
            log.info("Received shutdown signal")
            break
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)
            send_heartbeat()
            time.sleep(300)


if __name__ == '__main__':
    run()