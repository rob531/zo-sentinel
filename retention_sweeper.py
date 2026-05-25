import os
import time
import signal
import requests
import psutil
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import atexit

SERVICE_NAME = "retention_sweeper"
SERVICE_PORT = 8791
WRITE_SERVICE = "http://127.0.0.1:8772"
WRITE_SERVICE_URL = f"{WRITE_SERVICE}/write"
QUERY_URL = f"{WRITE_SERVICE}/query"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
RETENTION_DAYS = 30

TABLES_WITH_EVIDENCE_BLOB = [
    {
        "table": "mcp_signal_scores",
        "timestamp_column": "scored_at",
        "evidence_column": "evidence"
    },
    {
        "table": "mcp_signal_enrichments",
        "timestamp_column": "created_at",
        "evidence_column": "evidence_blob"
    }
]

PROTECTED_TABLES = [
    "mcp_server_registry",
    "mcp_attestations",
    "mcp_threat_associations",
    "mcp_risk_register",
    "mcp_decisions",
    "mesh_events",
    "audit_log",
    "auth_tokens",
    "service_health"
]


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        if psutil.pid_exists(old_pid):
            print(f"Instance already running with PID {old_pid}")
            return False
        else:
            print(f"Removing stale PID file from {old_pid}")
            os.remove(PID_FILE)
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def signal_handler(signum, frame):
    remove_pid_file()
    exit(0)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Query error: {e}")
        return {"rows": [], "count": 0, "error": str(e)}


def ws_write(table: str, rows: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Write error: {e}")
        return {"ok": False, "error": str(e)}


def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Execute error: {e}")
        return {"ok": False, "error": str(e)}


def send_heartbeat():
    heartbeat_data = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now().isoformat()
    }
    ws_write("service_health", heartbeat_data)


def get_expired_records_query(table: str, timestamp_column: str, evidence_column: str, limit: int = 1000) -> str:
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
    
    if timestamp_column in ["created_at", "scored_at", "updated_at", "captured_at", "reported_at"]:
        return f"""
            SELECT rowid, {timestamp_column}
            FROM {table}
            WHERE {timestamp_column} IS NOT NULL
              AND {timestamp_column} < '{cutoff_str}'
              AND ({evidence_column} IS NOT NULL AND {evidence_column} != '' AND {evidence_column} != 'null')
            LIMIT {limit}
        """
    else:
        return f"""
            SELECT rowid, {timestamp_column}
            FROM {table}
            WHERE {timestamp_column} < '{cutoff_str}'
            LIMIT {limit}
        """


def delete_expired_evidence_sql(table: str, timestamp_column: str, evidence_column: str, batch_size: int = 500) -> str:
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
    
    if evidence_column in ["evidence", "evidence_blob", "raw_data", "payload", "details"]:
        return f"""
            DELETE FROM {table}
            WHERE {timestamp_column} IS NOT NULL
              AND {timestamp_column} < '{cutoff_str}'
              AND ({evidence_column} IS NOT NULL AND {evidence_column} != '' AND {evidence_column} != 'null')
            LIMIT {batch_size}
        """
    else:
        return f"""
            DELETE FROM {table}
            WHERE {timestamp_column} < '{cutoff_str}'
            LIMIT {batch_size}
        """


def get_table_record_count(table: str) -> int:
    result = ws_query(f"SELECT COUNT(*) as cnt FROM {table}")
    if result.get("rows"):
        return result["rows"][0].get("cnt", 0)
    return 0


def get_table_blob_count(table: str, timestamp_column: str, evidence_column: str) -> int:
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
    
    query = f"""
        SELECT COUNT(*) as cnt
        FROM {table}
        WHERE {timestamp_column} IS NOT NULL
          AND {timestamp_column} < '{cutoff_str}'
          AND ({evidence_column} IS NOT NULL AND {evidence_column} != '' AND {evidence_column} != 'null')
    """
    result = ws_query(query)
    if result.get("rows"):
        return result["rows"][0].get("cnt", 0)
    return 0


def process_table(table_config: Dict[str, str]) -> Dict[str, Any]:
    table = table_config["table"]
    timestamp_column = table_config["timestamp_column"]
    evidence_column = table_config["evidence_column"]
    
    result = {
        "table": table,
        "timestamp_column": timestamp_column,
        "evidence_column": evidence_column,
        "status": "success",
        "total_records": 0,
        "expired_blobs": 0,
        "deleted": 0,
        "errors": []
    }
    
    try:
        result["total_records"] = get_table_record_count(table)
        result["expired_blobs"] = get_table_blob_count(table, timestamp_column, evidence_column)
        
        if result["expired_blobs"] == 0:
            print(f"  [{table}] No expired evidence blobs to delete")
            return result
        
        print(f"  [{table}] Found {result['expired_blobs']} expired evidence blobs (>{RETENTION_DAYS} days)")
        
        total_deleted = 0
        max_iterations = 100
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            delete_sql = delete_expired_evidence_sql(table, timestamp_column, evidence_column, batch_size=500)
            exec_result = ws_execute(delete_sql)
            
            if exec_result.get("ok"):
                rows_affected = exec_result.get("rows_affected", exec_result.get("count", 0))
                if rows_affected == 0:
                    break
                total_deleted += rows_affected
                print(f"  [{table}] Iteration {iteration}: deleted {rows_affected} rows (total: {total_deleted})")
            else:
                error_msg = exec_result.get("error", "Unknown error")
                result["errors"].append(f"Iteration {iteration}: {error_msg}")
                print(f"  [{table}] Delete error: {error_msg}")
                break
            
            time.sleep(0.5)
        
        result["deleted"] = total_deleted
        
        if total_deleted > 0:
            print(f"  [{table}] Completed: deleted {total_deleted} expired evidence blobs")
        
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))
        print(f"  [{table}] Error processing table: {e}")
    
    return result


def run_retention_sweep() -> Dict[str, Any]:
    print(f"\n{datetime.now().isoformat()} Starting retention sweep (30-day SLA)")
    print(f"Retention policy: Evidence blobs older than {RETENTION_DAYS} days")
    print(f"Protected tables (not processed): {', '.join(PROTECTED_TABLES)}")
    
    results = []
    total_deleted = 0
    total_expired = 0
    
    for table_config in TABLES_WITH_EVIDENCE_BLOB:
        table = table_config["table"]
        if table in PROTECTED_TABLES:
            print(f"  [{table}] SKIPPED (protected table)")
            continue
        
        print(f"\nProcessing table: {table}")
        result = process_table(table)
        results.append(result)
        
        if result["status"] == "success":
            total_deleted += result.get("deleted", 0)
            total_expired += result.get("expired_blobs", 0)
    
    summary = {
        "timestamp": datetime.now().isoformat(),
        "retention_days": RETENTION_DAYS,
        "tables_processed": len([r for r in results if r["status"] == "success"]),
        "tables_failed": len([r for r in results if r["status"] == "error"]),
        "total_records_checked": sum(r.get("total_records", 0) for r in results),
        "total_expired_blobs_found": total_expired,
        "total_deleted": total_deleted,
        "table_results": results
    }
    
    print(f"\n{datetime.now().isoformat()} Retention sweep completed")
    print(f"  Tables processed: {summary['tables_processed']}")
    print(f"  Total expired blobs found: {total_expired}")
    print(f"  Total deleted: {total_deleted}")
    
    return summary


def ensure_service_health_table():
    sql = """
        CREATE TABLE IF NOT EXISTS service_health (
            service VARCHAR PRIMARY KEY,
            last_heartbeat TIMESTAMP
        )
    """
    ws_execute(sql)


def ensure_retention_log_table():
    sql = """
        CREATE TABLE IF NOT EXISTS IF NOT EXISTS retention_log (
            id INTEGER PRIMARY KEY,
            run_timestamp TIMESTAMP,
            table_name VARCHAR,
            records_deleted INTEGER,
            status VARCHAR,
            error_message TEXT
        )
    """
    ws_execute(sql)


def log_retention_run(summary: Dict[str, Any]):
    for table_result in summary.get("table_results", []):
        log_entry = {
            "run_timestamp": summary["timestamp"],
            "table_name": table_result["table"],
            "records_deleted": table_result.get("deleted", 0),
            "status": table_result["status"],
            "error_message": "; ".join(table_result.get("errors", [])) if table_result.get("errors") else None
        }
        ws_write("retention_log", log_entry)


def cycle():
    try:
        ensure_service_health_table()
        ensure_retention_log_table()
        
        summary = run_retention_sweep()
        log_retention_run(summary)
        
        return summary
    except Exception as e:
        print(f"Error in retention cycle: {e}")
        return {"status": "error", "error": str(e)}


def run():
    print(f"{SERVICE_NAME} starting...")
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    atexit.register(remove_pid_file)
    
    if not check_single_instance():
        print(f"Another instance is running. Exiting.")
        return
    
    write_pid()
    print(f"PID {os.getpid()} written to {PID_FILE}")
    
    start_time = time.time()
    last_sweep = 0
    last_heartbeat = 0
    
    SWEEP_INTERVAL = 86400
    
    while True:
        try:
            current_time = time.time()
            
            if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = current_time
                print(f"Heartbeat sent at {datetime.now().isoformat()}")
            
            if current_time - last_sweep >= SWEEP_INTERVAL:
                print(f"\nScheduled retention sweep triggered")
                cycle()
                last_sweep = current_time
            
            time.sleep(30)
            
        except KeyboardInterrupt:
            print("\nShutdown signal received")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(60)
    
    remove_pid_file()
    print(f"{SERVICE_NAME} stopped. Uptime: {time.time() - start_time:.0f}s")


if __name__ == "__main__":
    run()