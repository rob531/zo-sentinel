#!/usr/bin/env python3
"""
write_service_startup.py
Bootstrap script that initializes write_service heartbeat and creates all core DuckDB tables.
Executes via POST http://127.0.0.1:8772/execute
"""

import sys
import time
import requests
import os
from datetime import datetime

WRITE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEALTH_URL = "http://127.0.0.1:8772/health"
SERVICE_NAME = "write_service_startup"

def check_single_instance():
    """Ensure only one instance runs"""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"Another instance is running (PID {old_pid}). Exiting.")
            sys.exit(0)
        except OSError:
            pass
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

def send_heartbeat():
    """Send heartbeat via execute endpoint"""
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={
            "sql": f"INSERT INTO service_health (service, last_heartbeat) VALUES ('{SERVICE_NAME}', '{datetime.now().isoformat()}') ON CONFLICT (service) DO UPDATE SET last_heartbeat = excluded.last_heartbeat"
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Heartbeat failed: {e}")
        return False

def execute_sql(sql):
    """Execute SQL via write_service execute endpoint"""
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"sql": sql}, timeout=30)
        if resp.status_code == 200:
            return True
        print(f"SQL execute failed: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        print(f"Execute error: {e}")
        return False

def create_table_if_not_exists(table_name, columns):
    """Create a table if it doesn't exist"""
    sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})"
    return execute_sql(sql)

def init_service_health():
    """Initialize service_health table for heartbeat tracking"""
    sql = """
    CREATE TABLE IF NOT EXISTS service_health (
        service VARCHAR PRIMARY KEY,
        last_heartbeat TIMESTAMP
    )
    """
    return execute_sql(sql)

def init_mcp_server_registry():
    """Create mcp_server_registry table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        url VARCHAR,
        description TEXT,
        trust_score FLOAT DEFAULT 0.0,
        verdict VARCHAR DEFAULT 'UNKNOWN',
        registry_source VARCHAR DEFAULT 'manual',
        scan_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    return create_table_if_not_exists("mcp_server_registry", sql)

def init_mcp_signal_scores():
    """Create mcp_signal_scores table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        server_id VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score FLOAT DEFAULT 0.0,
        evidence TEXT,
        scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, signal_name)
    )
    """
    return create_table_if_not_exists("mcp_signal_scores", sql)

def init_mcp_signal_enrichments():
    """Create mcp_signal_enrichments table for additional enrichment data"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR NOT NULL,
        enrichment_type VARCHAR NOT NULL,
        enrichment_data TEXT,
        source_url VARCHAR,
        enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, enrichment_type)
    )
    """
    return create_table_if_not_exists("mcp_signal_enrichments", sql)

def init_mcp_threat_associations():
    """Create mcp_threat_associations table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        server_id VARCHAR NOT NULL,
        threat_type VARCHAR NOT NULL,
        severity VARCHAR,
        evidence TEXT,
        reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, threat_type)
    )
    """
    return create_table_if_not_exists("mcp_threat_associations", sql)

def init_mcp_risk_register():
    """Create mcp_risk_register table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_risk_register (
        server_id VARCHAR PRIMARY KEY,
        risk_tier VARCHAR DEFAULT 'UNKNOWN',
        risk_rank INTEGER,
        threat_count INTEGER DEFAULT 0,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    return create_table_if_not_exists("mcp_risk_register", sql)

def init_mcp_attestations():
    """Create mcp_attestations table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_attestations (
        server_id VARCHAR NOT NULL,
        attestation_type VARCHAR NOT NULL,
        attested_by VARCHAR,
        attested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        signature TEXT,
        PRIMARY KEY (server_id, attestation_type)
    )
    """
    return create_table_if_not_exists("mcp_attestations", sql)

def init_mcp_definition_history():
    """Create mcp_definition_history table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_definition_history (
        server_id VARCHAR NOT NULL,
        version INTEGER NOT NULL,
        definition_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, version)
    )
    """
    return create_table_if_not_exists("mcp_definition_history", sql)

def init_mcp_submissions():
    """Create mcp_submissions table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_submissions (
        submission_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        mcp_name VARCHAR NOT NULL,
        submitted_by VARCHAR,
        status VARCHAR DEFAULT 'PENDING',
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TIMESTAMP
    )
    """
    return create_table_if_not_exists("mcp_submissions", sql)

def init_mcp_exemptions():
    """Create mcp_exemptions table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_exemptions (
        server_id VARCHAR PRIMARY KEY,
        exemption_reason TEXT,
        exempted_by VARCHAR,
        exempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )
    """
    return create_table_if_not_exists("mcp_exemptions", sql)

def init_mcp_decisions():
    """Create mcp_decisions table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_decisions (
        decision_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        decision_type VARCHAR NOT NULL,
        decision VARCHAR,
        decided_by VARCHAR,
        decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        notes TEXT
    )
    """
    return create_table_if_not_exists("mcp_decisions", sql)

def init_mcp_policy_rules():
    """Create mcp_policy_rules table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_policy_rules (
        rule_id VARCHAR PRIMARY KEY,
        rule_name VARCHAR NOT NULL,
        rule_expression TEXT,
        action VARCHAR,
        enabled BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    return create_table_if_not_exists("mcp_policy_rules", sql)

def init_mcp_fingerprints():
    """Create mcp_fingerprints table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_fingerprints (
        server_id VARCHAR NOT NULL,
        fingerprint_type VARCHAR NOT NULL,
        fingerprint_hash VARCHAR NOT NULL,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, fingerprint_type)
    )
    """
    return create_table_if_not_exists("mcp_fingerprints", sql)

def init_mcp_tool_hashes():
    """Create mcp_tool_hashes table"""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_tool_hashes (
        server_id VARCHAR NOT NULL,
        tool_name VARCHAR NOT NULL,
        tool_hash VARCHAR NOT NULL,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, tool_name)
    )
    """
    return create_table_if_not_exists("mcp_tool_hashes", sql)

def init_audit_log():
    """Create audit_log table"""
    sql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY,
        target_server_id VARCHAR,
        event_type VARCHAR NOT NULL,
        actor VARCHAR,
        detail TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    return create_table_if_not_exists("audit_log", sql)

def init_auth_tokens():
    """Create auth_tokens table for API authentication"""
    sql = """
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token_id VARCHAR PRIMARY KEY,
        action VARCHAR NOT NULL,
        mcp_name VARCHAR,
        submission_id VARCHAR,
        admin_email VARCHAR,
        expires_at TIMESTAMP,
        used BOOLEAN DEFAULT FALSE,
        used_at TIMESTAMP
    )
    """
    return create_table_if_not_exists("auth_tokens", sql)

def create_indexes():
    """Create indexes for performance"""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_signal_scores_server_id ON mcp_signal_scores(server_id)",
        "CREATE INDEX IF NOT EXISTS idx_signal_scores_signal ON mcp_signal_scores(signal_name)",
        "CREATE INDEX IF NOT EXISTS idx_threat_server ON mcp_threat_associations(server_id)",
        "CREATE INDEX IF NOT EXISTS idx_threat_severity ON mcp_threat_associations(severity)",
        "CREATE INDEX IF NOT EXISTS idx_risk_tier ON mcp_risk_register(risk_tier)",
        "CREATE INDEX IF NOT EXISTS idx_registry_verdict ON mcp_server_registry(verdict)",
        "CREATE INDEX IF NOT EXISTS idx_registry_source ON mcp_server_registry(registry_source)",
        "CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_server_id)",
        "CREATE INDEX IF NOT EXISTS idx_fingerprints_server ON mcp_fingerprints(server_id)",
        "CREATE INDEX IF NOT EXISTS idx_tool_hashes_server ON mcp_tool_hashes(server_id)",
        "CREATE INDEX IF NOT EXISTS idx_attestations_server ON mcp_attestations(server_id)",
    ]
    for idx_sql in indexes:
        execute_sql(idx_sql)

def main():
    """Main bootstrap function"""
    check_single_instance()
    
    print(f"[{datetime.now().isoformat()}] ZO-SENTINEL write_service startup bootstrap")
    print("=" * 60)
    
    # Wait for write_service to be ready
    print("Checking write_service health...")
    max_retries = 30
    retry_count = 0
    while retry_count < max_retries:
        try:
            resp = requests.get(HEALTH_URL, timeout=5)
            if resp.status_code == 200:
                print("write_service is healthy")
                break
        except Exception:
            pass
        retry_count += 1
        print(f"Waiting for write_service... ({retry_count}/{max_retries})")
        time.sleep(2)
    
    if retry_count >= max_retries:
        print("ERROR: write_service not available after 60 seconds")
        return 1
    
    # Initialize tables
    print("\nInitializing tables...")
    
    tables = [
        ("service_health", init_service_health),
        ("auth_tokens", init_auth_tokens),
        ("mcp_server_registry", init_mcp_server_registry),
        ("mcp_signal_scores", init_mcp_signal_scores),
        ("mcp_signal_enrichments", init_mcp_signal_enrichments),
        ("mcp_threat_associations", init_mcp_threat_associations),
        ("mcp_risk_register", init_mcp_risk_register),
        ("mcp_attestations", init_mcp_attestations),
        ("mcp_definition_history", init_mcp_definition_history),
        ("mcp_submissions", init_mcp_submissions),
        ("mcp_exemptions", init_mcp_exemptions),
        ("mcp_decisions", init_mcp_decisions),
        ("mcp_policy_rules", init_mcp_policy_rules),
        ("mcp_fingerprints", init_mcp_fingerprints),
        ("mcp_tool_hashes", init_mcp_tool_hashes),
        ("audit_log", init_audit_log),
    ]
    
    success_count = 0
    for table_name, init_fn in tables:
        try:
            if init_fn():
                print(f"  [OK] {table_name}")
                success_count += 1
            else:
                print(f"  [FAIL] {table_name}")
        except Exception as e:
            print(f"  [ERROR] {table_name}: {e}")
    
    # Create indexes
    print("\nCreating indexes...")
    create_indexes()
    print("  [OK] indexes created")
    
    # Initial heartbeat
    print("\nSending startup heartbeat...")
    if send_heartbeat():
        print("  [OK] heartbeat recorded")
    else:
        print("  [WARN] heartbeat failed (table may not exist yet)")
    
    print("\n" + "=" * 60)
    print(f"Bootstrap complete: {success_count}/{len(tables)} tables initialized")
    print("=" * 60)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())