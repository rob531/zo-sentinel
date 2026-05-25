#!/usr/bin/env python3
"""
mcp_age_risk_scorer.py - ZO-SENTINEL MCP package age risk scorer daemon.
Monitors package age from npm registry and assigns temporal_stability scores.
"""
import os, sys, time, logging, requests, json, hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

log = logging.getLogger(__name__)

SERVICE_NAME = "mcp_age_risk_scorer"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 86400

def get_write_url() -> str:
    return os.environ.get("WRITE_SERVICE_URL", WRITE_SERVICE_URL)

def get_execute_url() -> str:
    return os.environ.get("EXECUTE_URL", EXECUTE_URL)

def get_query_url() -> str:
    return os.environ.get("QUERY_URL", QUERY_URL)

def get_db_path() -> str:
    return os.environ.get("DB_PATH", "/var/lib/zo_sentinel/sentinel.db")

def check_single_instance() -> bool:
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                log.warning(f"{SERVICE_NAME} already running with PID {old_pid}")
                return False
        except (ValueError, IOError):
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True

def remove_pid_file():
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        os.remove(pid_file)

def send_heartbeat(write_url: str):
    try:
        requests.post(
            write_url,
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "status": "running"
                }
            },
            timeout=10
        )
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

def ws_query(query: str, params: Optional[List] = None) -> List[Dict[str, Any]]:
    """Query data from write_service."""
    try:
        url = get_query_url()
        payload = {"query": query}
        if params:
            payload["params"] = params
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return result if isinstance(result, list) else []
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []

def ws_write(table: str, rows: List[Dict[str, Any]], write_url: str):
    """Write data to write_service."""
    try:
        resp = requests.post(write_url, json={"table": table, "rows": rows}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Write to {table} failed: {e}")
        raise

def get_npm_package_info(package_name: str) -> Optional[Dict[str, Any]]:
    """Fetch npm registry info for a package."""
    try:
        url = f"https://registry.npmjs.org/{package_name}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.debug(f"Failed to fetch npm info for {package_name}: {e}")
        return None

def calculate_age_days(created_at: str) -> Optional[float]:
    """Calculate age in days from ISO timestamp."""
    try:
        created_at = created_at.replace('Z', '+00:00')
        created_dt = datetime.fromisoformat(created_at)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        age = now_dt - created_dt
        return age.total_seconds() / 86400
    except Exception as e:
        log.debug(f"Failed to parse created_at {created_at}: {e}")
        return None

def calculate_temporal_stability_score(age_days: float) -> float:
    """Calculate temporal_stability signal score based on package age."""
    if age_days < 7:
        return -25.0
    elif age_days < 30:
        return -15.0
    elif age_days <= 90:
        return -5.0
    elif age_days > 365:
        return 10.0
    else:
        return 0.0

def get_age_risk_tier(age_days: float, has_threats: bool, limited_downloads: bool) -> str:
    """Determine age_risk_tier based on package age and cross-references."""
    if age_days < 7 and has_threats:
        return "HIGH_RISK_ISOLATED"
    if age_days < 30 and limited_downloads:
        return "CAUTION_LIMITED"
    if age_days < 7:
        return "BRAND_NEW"
    if age_days < 30:
        return "NEW"
    if age_days < 90:
        return "MATURING"
    if age_days < 365:
        return "ESTABLISHED"
    return "VETERAN"

def has_threat_associations(server_id: str) -> bool:
    """Check if server has any threat associations."""
    query = """
        SELECT COUNT(*) as threat_count 
        FROM mcp_threat_associations 
        WHERE server_id = ? 
        AND reported_at > now() - INTERVAL '90 days'
    """
    results = ws_query(query, [server_id])
    if results and len(results) > 0:
        threat_count = results[0].get("threat_count", 0)
        return threat_count > 0
    return False

def get_server_downloads(package_name: str) -> int:
    """Get download count for npm package (approximate from weekly stats)."""
    try:
        url = f"https://api.npmjs.org/downloads/point/last-week/{package_name}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("downloads", 0)
    except Exception as e:
        log.debug(f"Failed to fetch downloads for {package_name}: {e}")
    return 0

def extract_package_name(server: Dict[str, Any]) -> Optional[str]:
    """Extract package name from server record."""
    name = server.get("name", "")
    if not name:
        return None
    if name.startswith("@"):
        parts = name.split("/")
        if len(parts) == 2:
            return parts[1]
    return name

def get_servers_to_score() -> List[Dict[str, Any]]:
    """Get servers from registry that should be scored for age risk."""
    query = """
        SELECT server_id, name, url, registry_source
        FROM mcp_server_registry
        WHERE (registry_source = 'npm' OR url LIKE '%npmjs%' OR name LIKE '@%')
        AND (trust_score IS NULL OR trust_score = 0 OR trust_score < 0.5)
        ORDER BY last_seen DESC
        LIMIT 500
    """
    return ws_query(query)

def calculate_age_risk(
    package_name: str,
    server_id: str,
    age_days: float,
    download_count: int
) -> Dict[str, Any]:
    """Calculate complete age risk assessment for a package."""
    has_threats = has_threat_associations(server_id)
    limited_downloads = age_days < 30 and download_count < 10
    
    stability_score = calculate_temporal_stability_score(age_days)
    age_risk_tier = get_age_risk_tier(age_days, has_threats, limited_downloads)
    
    evidence_parts = [
        f"npm package created {age_days:.1f} days ago",
        f"approx weekly downloads: {download_count}"
    ]
    if has_threats:
        evidence_parts.append("server has recent threat associations")
    if limited_downloads:
        evidence_parts.append("limited adoption detected")
    
    evidence = "; ".join(evidence_parts)
    
    return {
        "stability_score": stability_score,
        "age_risk_tier": age_risk_tier,
        "age_days": round(age_days, 2),
        "download_count": download_count,
        "has_threats": has_threats,
        "limited_downloads": limited_downloads,
        "evidence": evidence
    }

def write_signal_scores(server_id: str, assessment: Dict[str, Any], write_url: str):
    """Write temporal_stability signal to mcp_signal_scores."""
    rows = [{
        "server_id": server_id,
        "signal_name": "temporal_stability",
        "score": assessment["stability_score"],
        "evidence": assessment["evidence"],
        "scored_at": datetime.now(timezone.utc).isoformat()
    }]
    ws_write("mcp_signal_scores", rows, write_url)

def write_risk_register(server_id: str, assessment: Dict[str, Any], write_url: str):
    """Write age_risk_tier to mcp_risk_register."""
    rows = [{
        "server_id": server_id,
        "risk_type": "age",
        "risk_tier": assessment["age_risk_tier"],
        "age_days": assessment["age_days"],
        "has_threats": assessment["has_threats"],
        "limited_downloads": assessment["limited_downloads"],
        "score": assessment["stability_score"],
        "details": assessment["evidence"],
        "recorded_at": datetime.now(timezone.utc).isoformat()
    }]
    try:
        ws_write("mcp_risk_register", rows, write_url)
    except Exception as e:
        log.debug(f"mcp_risk_register write failed (table may not exist): {e}")

def process_server(server: Dict[str, Any], write_url: str) -> bool:
    """Process a single server and write age-based risk assessment."""
    server_id = server.get("server_id") or server.get("name")
    package_name = extract_package_name(server)
    
    if not server_id:
        log.warning("Server missing server_id and name, skipping")
        return False
    
    if not package_name:
        log.debug(f"Cannot extract package name for {server_id}, skipping")
        return False
    
    npm_info = get_npm_package_info(package_name)
    if not npm_info:
        log.debug(f"No npm registry data for {package_name}")
        return False
    
    time_created = npm_info.get("time", {}).get("created")
    if not time_created:
        log.debug(f"No creation time for npm package {package_name}")
        return False
    
    age_days = calculate_age_days(time_created)
    if age_days is None:
        log.debug(f"Failed to calculate age for {package_name}")
        return False
    
    download_count = get_server_downloads(package_name)
    
    assessment = calculate_age_risk(package_name, server_id, age_days, download_count)
    
    write_signal_scores(server_id, assessment, write_url)
    write_risk_register(server_id, assessment, write_url)
    
    log.info(
        f"Processed {server_id}: age={age_days:.1f}d, "
        f"tier={assessment['age_risk_tier']}, "
        f"stability={assessment['stability_score']}"
    )
    return True

def ensure_risk_register_table(write_url: str):
    """Ensure mcp_risk_register table exists."""
    try:
        execute_url = get_execute_url()
        create_sql = """
            CREATE TABLE IF NOT EXISTS mcp_risk_register (
                id BIGINT PRIMARY KEY,
                server_id VARCHAR NOT NULL,
                risk_type VARCHAR,
                risk_tier VARCHAR,
                age_days REAL,
                has_threats BOOLEAN,
                limited_downloads BOOLEAN,
                score REAL,
                details TEXT,
                recorded_at TIMESTAMPTZ DEFAULT now()
            )
        """
        resp = requests.post(execute_url, json={"sql": create_sql}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.debug(f"Table creation check failed (may already exist): {e}")

def run_cycle():
    """Execute one assessment cycle."""
    write_url = get_write_url()
    
    ensure_risk_register_table(write_url)
    
    servers = get_servers_to_score()
    log.info(f"Found {len(servers)} servers to process for age risk")
    
    processed = 0
    failed = 0
    
    for server in servers:
        try:
            if process_server(server, write_url):
                processed += 1
            else:
                failed += 1
        except Exception as e:
            log.error(f"Failed to process server {server.get('server_id')}: {e}")
            failed += 1
        
        time.sleep(0.5)
    
    log.info(f"Cycle complete: processed={processed}, failed/skipped={failed}")
    return processed, failed

def run():
    """Main daemon loop."""
    if not check_single_instance():
        log.error(f"{SERVICE_NAME} already running, exiting")
        return
    
    log.info(f"{SERVICE_NAME} starting...")
    
    try:
        cycle_count = 0
        while True:
            cycle_count += 1
            start_time = time.time()
            
            try:
                processed, failed = run_cycle()
                duration = time.time() - start_time
                log.info(
                    f"Cycle #{cycle_count} completed in {duration:.1f}s: "
                    f"processed={processed}, failed={failed}"
                )
            except Exception as e:
                log.error(f"Cycle #{cycle_count} failed: {e}")
            
            send_heartbeat(get_write_url())
            
            log.info(f"Sleeping for {POLL_INTERVAL}s until next cycle")
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        log.info(f"{SERVICE_NAME} received shutdown signal")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    run()