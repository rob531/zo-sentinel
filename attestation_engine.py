#!/usr/bin/env python3
"""
attestation_engine.py -- ZO-SENTINEL Phase 4: Attestation Engine
Generates formal attestations for MCP servers based on trust synthesis data.
Writes to mcp_attestations table and generates ATTESTATION_REPORT.md.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import requests

# Configuration
SERVICE_NAME = 'attestation_engine'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 21600  # 6 hours
LOG_DIR = '/home/workspace/zo_sentinel/logs'
REPORT_PATH = '/home/workspace/zo_sentinel/ATTESTATION_REPORT.md'

# Verdict to expiry mapping (days)
VERDICT_EXPIRY = {
    'TRUSTED_GENERAL': 90,
    'TRUSTED_RESEARCH': 60,
    'ENTERPRISE_CONTROLLED': 60,
    'CAUTION_LIMITED': 30,
    'HIGH_RISK_ISOLATED': 7,
    'KNOWN_THREAT': 7,
    'INSUFFICIENT': 14
}

# Verdict descriptions
VERDICT_DESCRIPTIONS = {
    'TRUSTED_GENERAL': 'Likely safe for enterprise use under formal security controls',
    'TRUSTED_RESEARCH': 'Suitable for research and development environments',
    'ENTERPRISE_CONTROLLED': 'Approved for controlled use with appropriate governance',
    'CAUTION_LIMITED': 'Use with caution in isolated, non-production environments',
    'CAUTION_ELEVATED': 'Elevated risk indicators require additional monitoring',
    'HIGH_RISK_ISOLATED': 'High risk profile - use only in isolated contexts without sensitive data',
    'KNOWN_THREAT': 'Do not deploy - known threat indicators present',
    'INSUFFICIENT': 'Insufficient data for determination - requires manual review'
}

# Logging setup
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/attestation_engine.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


def get_write_url():
    return WRITE_SERVICE_URL


def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        requests.post(WRITE_SERVICE_URL, json={
            'table': 'service_health',
            'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()},
            'wait': True
        })
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def ws_query(sql: str, params: list = None) -> list:
    """Execute SELECT via write_service /query. Normalizes response to list."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        if 'rows' in body:
            return body['rows']
        if 'results' in body:
            return body['results']
    return []


def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> dict:
    """Write to DuckDB via write_service."""
    url = WRITE_SERVICE_URL  # already ends in /write
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def fetch_server_data(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch trust_score, verdict, confidence from mcp_server_registry."""
    sql = """
    SELECT server_id, name, trust_score, verdict, verdict_reasoning, confidence, risk_tier, last_assessed
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    results = ws_query(sql, [server_id])
    if results and len(results) > 0:
        return results[0]
    return None


def fetch_risk_tier(server_id: str) -> Optional[str]:
    """Fetch risk_tier from mcp_risk_register if available."""
    try:
        sql = """
        SELECT risk_tier
        FROM mcp_risk_register
        WHERE server_id = ?
        ORDER BY assessed_at DESC
        LIMIT 1
        """
        results = ws_query(sql, [server_id])
        if results and len(results) > 0:
            return results[0].get('risk_tier')
    except Exception as e:
        logger.debug(f"No risk_tier found in mcp_risk_register for {server_id}: {e}")
    return None


def build_attestation_text(verdict: str, server_name: str, trust_score: float, confidence: float) -> str:
    """Build formal attestation text based on verdict."""
    description = VERDICT_DESCRIPTIONS.get(verdict, VERDICT_DESCRIPTIONS['INSUFFICIENT'])
    
    lines = [
        f"# MCP Server Attestation",
        f"## Server: {server_name}",
        f"## Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"### Verdict: {verdict}",
        "",
        f"**Assessment**: {description}",
        "",
        f"### Quantitative Indicators",
        f"- Trust Score: {trust_score:.1f}/100",
        f"- Confidence Level: {confidence:.1%}",
        "",
        f"### Caveats",
        "- This attestation is based on automated analysis only",
        "- This document does not constitute a formal security audit",
        "- Conditions may change; attestations have defined validity periods",
        "- Organizations should perform their own risk assessment",
        ""
    ]
    return "\n".join(lines)


def generate_attestation(server_id: str) -> Optional[Dict[str, Any]]:
    """Generate attestation for a given server_id.
    
    Returns:
        dict with attestation data or None if server not found
    """
    logger.info(f"Generating attestation for server: {server_id}")
    
    # Fetch server data
    server_data = fetch_server_data(server_id)
    if not server_data:
        logger.warning(f"Server {server_id} not found in registry")
        return None
    
    verdict = server_data.get('verdict', 'INSUFFICIENT')
    trust_score = server_data.get('trust_score', 0)
    confidence = server_data.get('confidence', 0)
    server_name = server_data.get('name', server_id)
    
    # Get risk_tier from risk register if available
    risk_tier = fetch_risk_tier(server_id)
    if not risk_tier:
        # Fallback: derive from trust_score
        if trust_score >= 75:
            risk_tier = 'LOW'
        elif trust_score >= 45:
            risk_tier = 'MEDIUM'
        elif trust_score >= 15:
            risk_tier = 'HIGH'
        else:
            risk_tier = 'CRITICAL'
    
    # Determine expiry based on verdict
    expiry_days = VERDICT_EXPIRY.get(verdict, 14)
    valid_until = datetime.now(timezone.utc) + timedelta(days=expiry_days)
    
    # Build attestation text
    attestation_text = build_attestation_text(verdict, server_name, trust_score, confidence)
    
    # Determine scope based on verdict
    if verdict == 'TRUSTED_GENERAL':
        scope = 'General enterprise use permitted'
    elif verdict == 'TRUSTED_RESEARCH':
        scope = 'Research and development environments only'
    elif verdict == 'ENTERPRISE_CONTROLLED':
        scope = 'Controlled use with governance controls required'
    elif verdict == 'CAUTION_LIMITED':
        scope = 'Isolated environments only, enhanced monitoring required'
    elif verdict == 'HIGH_RISK_ISOLATED':
        scope = 'Strictly isolated contexts, no sensitive data'
    elif verdict == 'KNOWN_THREAT':
        scope = 'No deployment authorized'
    else:
        scope = 'Manual review required before deployment'
    
    # Determine confidence level
    if confidence >= 0.85:
        confidence_level = 'HIGH'
    elif confidence >= 0.60:
        confidence_level = 'MEDIUM'
    else:
        confidence_level = 'LOW'
    
    # Standard caveats
    caveats = "Automated analysis only; not a formal security audit; verify conditions before deployment"
    
    attestation = {
        'server_id': server_id,
        'attestation_text': attestation_text,
        'scope': scope,
        'confidence_level': confidence_level,
        'valid_until': valid_until.isoformat(),
        'risk_tier': risk_tier,
        'caveats': caveats,
        'generated_at': datetime.now(timezone.utc).isoformat()
    }
    
    return attestation


def create_attestations_table():
    """Create mcp_attestations table if not exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_attestations (
        id                BIGINT PRIMARY KEY,
        server_id         VARCHAR NOT NULL,
        attestation_text  TEXT,
        scope             VARCHAR,
        confidence_level  VARCHAR,
        valid_until       TIMESTAMPTZ,
        risk_tier         VARCHAR,
        caveats           TEXT,
        generated_at      TIMESTAMPTZ DEFAULT now(),
        UNIQUE(server_id, generated_at)
    )
    """
    try:
        requests.post(EXECUTE_URL, json={'sql': sql})
        logger.info("mcp_attestations table created or verified")
    except Exception as e:
        logger.error(f"Failed to create attestations table: {e}")


def write_attestation(attestation: Dict[str, Any]) -> dict:
    """Write attestation to mcp_attestations table."""
    return ws_write('mcp_attestations', attestation, wait=True)


def generate_report(all_attestations: list):
    """Generate ATTESTATION_REPORT.md with all attestations."""
    lines = [
        "# ZO-SENTINEL Attestation Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Total Attestations: {len(all_attestations)}",
        "",
        "---",
        ""
    ]
    
    for att in all_attestations:
        lines.append(att.get('attestation_text', ''))
        lines.append("---")
        lines.append("")
    
    report_content = "\n".join(lines)
    
    with open(REPORT_PATH, 'w') as f:
        f.write(report_content)
    
    logger.info(f"Attestation report written to {REPORT_PATH}")


def get_all_servers_needing_attestation() -> list:
    """Get all servers that need attestation (no recent valid attestation)."""
    sql = """
    SELECT server_id, name, verdict, trust_score, confidence
    FROM mcp_server_registry
    WHERE verdict IS NOT NULL
    AND trust_score IS NOT NULL
    AND server_id NOT IN (
        SELECT server_id 
        FROM mcp_attestations 
        WHERE valid_until > now()
    )
    """
    try:
        return ws_query(sql)
    except Exception as e:
        logger.error(f"Failed to query servers: {e}")
        return []


def cycle():
    """Main work cycle - generate attestations for servers."""
    logger.info("Starting attestation cycle")
    
    # Ensure table exists
    create_attestations_table()
    
    # Get servers needing attestation
    servers = get_all_servers_needing_attestation()
    logger.info(f"Found {len(servers)} servers needing attestation")
    
    all_attestations = []
    
    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        
        try:
            attestation = generate_attestation(server_id)
            if attestation:
                write_attestation(attestation)
                all_attestations.append(attestation)
                logger.info(f"Generated attestation for {server_id}")
        except Exception as e:
            logger.error(f"Failed to generate attestation for {server_id}: {e}")
    
    # Generate consolidated report if we have attestations
    if all_attestations:
        generate_report(all_attestations)
    
    logger.info(f"Cycle complete. Generated {len(all_attestations)} attestations")


def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup():
        if os.path.exists(pid_file):
            os.remove(pid_file)
    
    import signal
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)


def run():
    """Main run loop with heartbeat and cycle management."""
    check_single_instance()
    logger.info(f"{SERVICE_NAME} starting")
    
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Error in cycle: {e}")
        
        send_heartbeat()
        time.sleep(CYCLE_INTERVAL)


if __name__ == '__main__':
    run()