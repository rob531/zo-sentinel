#!/usr/bin/env python3
"""BOOTSTRAP: Creates all core DuckDB tables via write_service HTTP API."""

import sys
import time
import requests

WRITE_SERVICE = "http://127.0.0.1:8772/execute"
MAX_RETRIES = 5
RETRY_DELAY = 2


def execute_ddl(sql: str) -> bool:
    """Execute DDL via write_service execute endpoint."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(WRITE_SERVICE, json={"sql": sql}, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("ok"):
                    return True
                print(f"[WARN] DDL not OK on attempt {attempt+1}: {result}")
            else:
                print(f"[WARN] HTTP {resp.status_code} on attempt {attempt+1}: {resp.text[:200]}")
        except requests.exceptions.ConnectionError as e:
            print(f"[WARN] Connection error on attempt {attempt+1}: {e}")
        except Exception as e:
            print(f"[WARN] Error on attempt {attempt+1}: {e}")
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    
    print(f"[FAIL] DDL failed after {MAX_RETRIES} attempts: {sql[:100]}")
    return False


def create_tables():
    """Create all core tables in dependency order."""
    
    tables = [
        # service_health - heartbeat tracking
        """CREATE TABLE IF NOT EXISTS service_health (
            service VARCHAR PRIMARY KEY,
            last_heartbeat TIMESTAMP
        )""",
        
        # mcp_server_registry - core server registry
        """CREATE TABLE IF NOT EXISTS mcp_server_registry (
            server_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            url VARCHAR,
            description VARCHAR,
            trust_score DOUBLE,
            verdict VARCHAR,
            registry_source VARCHAR,
            scan_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_signal_scores - threat signal scores
        """CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (server_id, signal_name)
        )""",
        
        # mcp_signal_enrichments - external data enrichments
        """CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            signal_name VARCHAR,
            source VARCHAR,
            enriched_data VARCHAR,
            enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_threat_associations - known threat links
        """CREATE TABLE IF NOT EXISTS mcp_threat_associations (
            server_id VARCHAR,
            threat_type VARCHAR,
            severity VARCHAR,
            evidence VARCHAR,
            reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (server_id, threat_type)
        )""",
        
        # mcp_risk_register - aggregated risk assessment
        """CREATE TABLE IF NOT EXISTS mcp_risk_register (
            server_id VARCHAR PRIMARY KEY,
            risk_tier VARCHAR,
            risk_rank INTEGER,
            threat_count INTEGER DEFAULT 0,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_attestations - third-party attestations
        """CREATE TABLE IF NOT EXISTS mcp_attestations (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            attested_by VARCHAR,
            attestation_type VARCHAR,
            statement VARCHAR,
            attested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_definition_history - version tracking
        """CREATE TABLE IF NOT EXISTS mcp_definition_history (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            definition_version VARCHAR,
            changes VARCHAR,
            defined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_submissions - intake queue
        """CREATE TABLE IF NOT EXISTS mcp_submissions (
            submission_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            submitted_by VARCHAR,
            status VARCHAR DEFAULT 'pending',
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )""",
        
        # mcp_exemptions - whitelisted servers
        """CREATE TABLE IF NOT EXISTS mcp_exemptions (
            server_id VARCHAR PRIMARY KEY,
            exemption_type VARCHAR,
            reason VARCHAR,
            exempted_by VARCHAR,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_decisions - audit trail of decisions
        """CREATE TABLE IF NOT EXISTS mcp_decisions (
            decision_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            decision_type VARCHAR,
            rationale VARCHAR,
            decided_by VARCHAR,
            decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_policy_rules - configurable policy rules
        """CREATE TABLE IF NOT EXISTS mcp_policy_rules (
            rule_id VARCHAR PRIMARY KEY,
            rule_name VARCHAR,
            conditions VARCHAR,
            action VARCHAR,
            enabled BOOLEAN DEFAULT TRUE,
            priority INTEGER DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_fingerprints - server fingerprints
        """CREATE TABLE IF NOT EXISTS mcp_fingerprints (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            fingerprint_type VARCHAR,
            value VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # mcp_tool_hashes - tool integrity hashes
        """CREATE TABLE IF NOT EXISTS mcp_tool_hashes (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            tool_name VARCHAR,
            hash_value VARCHAR,
            algorithm VARCHAR,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # audit_log - security audit trail
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            target_server_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # auth_tokens - authentication tokens
        """CREATE TABLE IF NOT EXISTS auth_tokens (
            token_id VARCHAR PRIMARY KEY,
            action VARCHAR,
            mcp_name VARCHAR,
            submission_id VARCHAR,
            admin_email VARCHAR,
            expires_at TIMESTAMP,
            used BOOLEAN DEFAULT FALSE,
            used_at TIMESTAMP
        )""",
    ]
    
    success_count = 0
    fail_count = 0
    
    print("[BOOTSTRAP] Creating core tables...")
    for sql in tables:
        table_name = sql.split("IF NOT EXISTS ")[1].split(" ")[0]
        print(f"  Creating {table_name}...", end=" ")
        if execute_ddl(sql):
            print("OK")
            success_count += 1
        else:
            print("FAIL")
            fail_count += 1
    
    print(f"\n[BOOTSTRAP] Complete: {success_count} created, {fail_count} failed")
    return fail_count == 0


def verify_tables():
    """Verify tables were created by querying write_service."""
    print("\n[BOOTSTRAP] Verifying tables...")
    
    query_url = "http://127.0.0.1:8772/query"
    expected_tables = [
        "service_health", "mcp_server_registry", "mcp_signal_scores",
        "mcp_signal_enrichments", "mcp_threat_associations", "mcp_risk_register",
        "mcp_attestations", "mcp_definition_history", "mcp_submissions",
        "mcp_exemptions", "mcp_decisions", "mcp_policy_rules",
        "mcp_fingerprints", "mcp_tool_hashes", "audit_log", "auth_tokens"
    ]
    
    for table in expected_tables:
        try:
            resp = requests.post(query_url, json={
                "sql": f"SELECT COUNT(*) as cnt FROM {table}"
            }, timeout=10)
            if resp.status_code == 200:
                print(f"  {table}: verified")
            else:
                print(f"  {table}: verification failed ({resp.status_code})")
        except Exception as e:
            print(f"  {table}: verification error - {e}")
    
    print("[BOOTSTRAP] Verification complete")


def main():
    """Main entry point."""
    print("=" * 60)
    print("ZO-SENTINEL Core Table Bootstrap")
    print("=" * 60)
    
    # Wait for write_service to be ready
    print("\nWaiting for write_service at 127.0.0.1:8772...")
    for i in range(MAX_RETRIES):
        try:
            requests.get("http://127.0.0.1:8772/health", timeout=5)
            print("write_service is ready")
            break
        except:
            if i < MAX_RETRIES - 1:
                print(f"  Attempt {i+1} failed, retrying...")
                time.sleep(RETRY_DELAY)
            else:
                print("ERROR: write_service not available")
                return 1
    
    if create_tables():
        verify_tables()
        print("\n" + "=" * 60)
        print("BOOTSTRAP SUCCESS")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("BOOTSTRAP FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())