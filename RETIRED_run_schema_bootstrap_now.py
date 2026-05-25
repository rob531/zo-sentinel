import os
import time
import logging
import requests
from typing import Dict, Any, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8773"
QUERY_URL = "http://127.0.0.1:8774"
SERVICE_NAME = "schema_bootstrap"
HEARTBEAT_INTERVAL = 30

PID_FILE = "/tmp/zo_sentinel_bootstrap.pid"


def get_db_path() -> str:
    return os.environ.get("DUCK_DB_PATH", "/tmp/zo_sentinel.db")


def get_write_url() -> str:
    return os.environ.get("WRITE_SERVICE_URL", WRITE_SERVICE_URL)


def get_execute_url() -> str:
    return os.environ.get("EXECUTE_URL", EXECUTE_URL)


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        old_pid = open(PID_FILE).read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Another instance running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            logger.info(f"Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def ws_execute(sql: str) -> Dict[str, Any]:
    url = f"{get_execute_url()}/execute"
    try:
        resp = requests.post(url, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return {"error": str(e)}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    url = get_write_url()
    try:
        resp = requests.post(url, json={"table": table, "rows": rows}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Write failed to {url}: {e}")
        return {"error": str(e)}


def ws_query(sql: str) -> List[Dict[str, Any]]:
    url = f"{QUERY_URL}/query"
    try:
        resp = requests.post(url, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []


def send_heartbeat() -> bool:
    try:
        result = ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "running"
        }])
        return "error" not in result
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False


def create_mcp_servers_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_servers (
        server_id VARCHAR PRIMARY KEY,
        server_name VARCHAR NOT NULL,
        namespace VARCHAR,
        registry_source VARCHAR,
        trust_score DOUBLE,
        risk_tier VARCHAR,
        verdict VARCHAR,
        verdict_reason TEXT,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP,
        tool_count INTEGER,
        has_authentication BOOLEAN,
        permission_count INTEGER,
        server_hash VARCHAR,
        metadata JSON
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create mcp_servers: {result['error']}")
        return False
    logger.info("Created mcp_servers table")
    return True


def create_signal_scores_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS signal_scores (
        server_id VARCHAR,
        signal_name VARCHAR,
        signal_value DOUBLE,
        weight DOUBLE,
        weighted_score DOUBLE,
        computed_at TIMESTAMP,
        PRIMARY KEY (server_id, signal_name)
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create signal_scores: {result['error']}")
        return False
    logger.info("Created signal_scores table")
    return True


def create_mesh_events_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mesh_events (
        event_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        event_type VARCHAR,
        event_data JSON,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status VARCHAR
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create mesh_events: {result['error']}")
        return False
    logger.info("Created mesh_events table")
    return True


def create_assessment_queue_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS assessment_queue (
        queue_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        submission_id VARCHAR,
        status VARCHAR,
        priority INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create assessment_queue: {result['error']}")
        return False
    logger.info("Created assessment_queue table")
    return True


def create_policy_rules_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS policy_rules (
        rule_id VARCHAR PRIMARY KEY,
        rule_name VARCHAR,
        rule_type VARCHAR,
        conditions JSON,
        action VARCHAR,
        severity VARCHAR,
        enabled BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create policy_rules: {result['error']}")
        return False
    logger.info("Created policy_rules table")
    return True


def create_service_health_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS service_health (
        service VARCHAR PRIMARY KEY,
        last_heartbeat TIMESTAMP,
        status VARCHAR,
        metadata JSON
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create service_health: {result['error']}")
        return False
    logger.info("Created service_health table")
    return True


def create_audit_trail_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS audit_trail (
        audit_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        action VARCHAR,
        actor VARCHAR,
        details JSON,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create audit_trail: {result['error']}")
        return False
    logger.info("Created audit_trail table")
    return True


def create_false_positive_corrections_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS false_positive_corrections (
        correction_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        original_verdict VARCHAR,
        corrected_verdict VARCHAR,
        analyst_id VARCHAR,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create false_positive_corrections: {result['error']}")
        return False
    logger.info("Created false_positive_corrections table")
    return True


def create_exemptions_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS exemptions (
        exemption_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        exemption_type VARCHAR,
        granted_by VARCHAR,
        reason TEXT,
        expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    result = ws_execute(sql)
    if "error" in result:
        logger.error(f"Failed to create exemptions: {result['error']}")
        return False
    logger.info("Created exemptions table")
    return True


def create_all_tables() -> Dict[str, bool]:
    tables = {
        "mcp_servers": create_mcp_servers_table,
        "signal_scores": create_signal_scores_table,
        "mesh_events": create_mesh_events_table,
        "assessment_queue": create_assessment_queue_table,
        "policy_rules": create_policy_rules_table,
        "service_health": create_service_health_table,
        "audit_trail": create_audit_trail_table,
        "false_positive_corrections": create_false_positive_corrections_table,
        "exemptions": create_exemptions_table,
    }
    results = {}
    for name, func in tables.items():
        logger.info(f"Creating table: {name}")
        results[name] = func()
    return results


def verify_table_exists(table_name: str) -> bool:
    sql = f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name = '{table_name}'"
    results = ws_query(sql)
    if results and results[0].get("cnt", 0) > 0:
        return True
    sql_check = f"DESCRIBE {table_name}"
    result = ws_execute(sql_check)
    if "error" not in result:
        return True
    return False


def verify_all_tables() -> Dict[str, bool]:
    expected_tables = [
        "mcp_servers",
        "signal_scores",
        "mesh_events",
        "assessment_queue",
        "policy_rules",
        "service_health",
        "audit_trail",
        "false_positive_corrections",
        "exemptions",
    ]
    results = {}
    for table in expected_tables:
        exists = verify_table_exists(table)
        results[table] = exists
        status = "OK" if exists else "MISSING"
        logger.info(f"Table {table}: {status}")
    return results


def insert_default_policies() -> bool:
    default_rules = [
        {
            "rule_id": "high-risk-perms",
            "rule_name": "High Risk Permissions",
            "rule_type": "permission",
            "conditions": {"permission_patterns": ["shell", "exec", "sudo", "admin"]},
            "action": "flag",
            "severity": "high",
            "enabled": True
        },
        {
            "rule_id": "no-auth-detected",
            "rule_name": "No Authentication",
            "rule_type": "authentication",
            "conditions": {"has_authentication": False},
            "action": "warn",
            "severity": "medium",
            "enabled": True
        },
        {
            "rule_id": "new-server-low-trust",
            "rule_name": "New Server Low Trust",
            "rule_type": "trust_score",
            "conditions": {"trust_score_max": 30, "age_days_max": 7},
            "action": "flag",
            "severity": "high",
            "enabled": True
        }
    ]
    result = ws_write("policy_rules", default_rules)
    if "error" in result:
        logger.warning(f"Failed to insert default policies: {result.get('error')}")
        return False
    logger.info("Inserted default policy rules")
    return True


def run_bootstrap(verify_only: bool = False) -> Dict[str, Any]:
    logger.info("=" * 60)
    logger.info("ZO-SENTINEL Schema Bootstrap Starting")
    logger.info("=" * 60)

    if not check_single_instance():
        return {"success": False, "error": "Another instance running"}

    if not verify_only:
        logger.info("Creating tables...")
        create_results = create_all_tables()
        success_count = sum(1 for v in create_results.values() if v)
        logger.info(f"Created {success_count}/{len(create_results)} tables")

        if success_count == len(create_results):
            logger.info("Inserting default policies...")
            insert_default_policies()
    else:
        logger.info("Verification mode - skipping creation")

    logger.info("Verifying tables...")
    verify_results = verify_all_tables()
    all_exist = all(verify_results.values())

    send_heartbeat()

    logger.info("=" * 60)
    if all_exist:
        logger.info("SCHEMA BOOTSTRAP COMPLETE - All tables verified")
    else:
        missing = [k for k, v in verify_results.items() if not v]
        logger.error(f"SCHEMA INCOMPLETE - Missing tables: {missing}")
    logger.info("=" * 60)

    return {
        "success": all_exist,
        "verified": verify_results,
        "created": create_results if not verify_only else {}
    }


def cleanup():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def run():
    try:
        result = run_bootstrap(verify_only=False)
        if result.get("success"):
            logger.info("Bootstrap completed successfully")
        else:
            logger.error("Bootstrap completed with errors")
    except KeyboardInterrupt:
        logger.info("Bootstrap interrupted")
    except Exception as e:
        logger.error(f"Bootstrap failed: {e}")
    finally:
        cleanup()


if __name__ == "__main__":
    run()