#!/usr/bin/env python3
"""
cross_registry_correlator.py -- ZO-SENTINEL cross-registry correlation daemon.
Cross-references MCP servers against multiple threat intelligence sources.
Polls every 86400s with heartbeat monitoring.
"""
import os
import time
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVICE_NAME = "cross_registry_correlator"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 86400
RATE_LIMIT_DELAY = 1.0

PHISHTANK_API_URL = "https://checkurl.phishtank.com/checkurl/"
ABUSEIPDB_API_URL = "https://api.abuseipdb.com/api/v2/check"
MALTIVERSE_API_URL = "https://api.maltiverse.com/hostname"
URLVOID_API_URL = "https://api.urlvoid.com/v1"

PHISHTANK_API_KEY = os.environ.get("PHISHTANK_API_KEY", "")
URLVOID_API_KEY = os.environ.get("URLVOID_API_KEY", "")
ABUSEIPDB_API_KEY = os.environ.get("ABUSEIPDB_API_KEY", "")
MALTIVERSE_API_KEY = os.environ.get("MALTIVERSE_API_KEY", "")

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_db_path() -> str:
    return os.environ.get("ZO_SENTINEL_DB", "/tmp/zo_sentinel.duckdb")


def check_single_instance() -> bool:
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.warning(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            logger.info(f"Stale PID file, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def send_heartbeat() -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
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
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")
        return False


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", data.get("data", []))
        return []
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    try:
        if isinstance(rows, dict):
            rows = [rows]
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return False


def rate_limited_request(func, *args, **kwargs):
    time.sleep(RATE_LIMIT_DELAY)
    return func(*args, **kwargs)


def check_phishtank(url: str) -> Optional[Dict[str, Any]]:
    if not PHISHTANK_API_KEY:
        return None
    try:
        resp = requests.post(
            PHISHTANK_API_URL,
            data={"url": url, "format": "json", "app_key": PHISHTANK_API_KEY},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results", {}).get("phish_detail_url"):
                return {
                    "source": "phishtank",
                    "threat_type": "phishing",
                    "severity": "CRITICAL",
                    "evidence": f"PhishTank confirmed phishing URL: {url}"
                }
    except Exception as e:
        logger.warning(f"PhishTank check failed: {e}")
    return None


def check_urlvoid(domain: str) -> Optional[Dict[str, Any]]:
    if not URLVOID_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{URLVOID_API_URL}/{URLVOID_API_KEY}/host/{domain}/",
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("detection", 0) > 0:
                return {
                    "source": "urlvoid",
                    "threat_type": "malicious_domain",
                    "severity": "CRITICAL",
                    "evidence": f"URLVoid detected {domain} as malicious (score: {data.get('detection')})"
                }
    except Exception as e:
        logger.warning(f"URLVoid check failed: {e}")
    return None


def check_abuseipdb(ip: str) -> Optional[Dict[str, Any]]:
    if not ABUSEIPDB_API_KEY:
        return None
    try:
        resp = requests.get(
            ABUSEIPDB_API_URL,
            params={"ipAddress": ip, "key": ABUSEIPDB_API_KEY},
            headers={"Accept": "application/json"},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            abuse_score = data.get("data", {}).get("abuseConfidenceScore", 0)
            if abuse_score >= 50:
                return {
                    "source": "abuseipdb",
                    "threat_type": "malicious_ip",
                    "severity": "CRITICAL",
                    "evidence": f"AbuseIPDB score {abuse_score} for IP {ip}"
                }
    except Exception as e:
        logger.warning(f"AbuseIPDB check failed: {e}")
    return None


def check_maltiverse(hostname: str) -> Optional[Dict[str, Any]]:
    if not MALTIVERSE_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{MALTIVERSE_API_URL}/{hostname}",
            headers={"Authorization": f"Bearer {MALTIVERSE_API_KEY}"},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("malicious", False) or data.get("classification", "") == "malicious":
                return {
                    "source": "maltiverse",
                    "threat_type": "malicious_hostname",
                    "severity": "CRITICAL",
                    "evidence": f"Maltiverse flagged hostname: {hostname}"
                }
    except Exception as e:
        logger.warning(f"Maltiverse check failed: {e}")
    return None


def check_world_articles(server_id: str) -> Optional[Dict[str, Any]]:
    try:
        sql = """
        SELECT title, topics, published_at, summary
        FROM world_articles
        WHERE (topics LIKE '%mcp%' OR topics LIKE '%model context protocol%')
        AND (title ILIKE '%malicious%' OR title ILIKE '%supply chain%' OR 
             title ILIKE '%backdoor%' OR title ILIKE '%trojan%' OR
             title ILIKE '%vulnerability%' OR title ILIKE '%exploit%')
        ORDER BY published_at DESC
        LIMIT 10
        """
        articles = ws_query(sql)
        server_hash = hashlib.sha256(server_id.encode()).hexdigest()[:16]
        for article in articles:
            if server_hash in (article.get("title", "") + article.get("summary", "")).lower():
                return {
                    "source": "world_articles",
                    "threat_type": "threat_intelligence_match",
                    "severity": "CRITICAL",
                    "evidence": f"World article match: {article.get('title', 'Unknown')} - {server_id}"
                }
    except Exception as e:
        logger.warning(f"World articles check failed: {e}")
    return None


def extract_domain_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        return None


def extract_ip_from_url(url: str) -> Optional[str]:
    import re
    if not url:
        return None
    ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    match = re.search(ip_pattern, url)
    if match:
        return match.group(0)
    return None


def update_server_verdict(server_id: str, verdict: str, reasoning: str) -> bool:
    try:
        return ws_write("mcp_server_registry", {
            "server_id": server_id,
            "verdict": verdict,
            "verdict_reasoning": reasoning,
            "last_assessed": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Failed to update verdict for {server_id}: {e}")
        return False


def record_threat_association(server_id: str, threat_data: Dict[str, Any]) -> bool:
    try:
        row = {
            "server_id": server_id,
            "threat_type": threat_data.get("threat_type", "unknown"),
            "evidence": threat_data.get("evidence", ""),
            "severity": threat_data.get("severity", "HIGH"),
            "reported_at": datetime.now(timezone.utc).isoformat()
        }
        return ws_write("mcp_threat_associations", row)
    except Exception as e:
        logger.error(f"Failed to record threat association: {e}")
        return False


def get_servers_to_check() -> List[Dict[str, Any]]:
    try:
        sql = """
        SELECT server_id, name, url, description, registry_source
        FROM mcp_server_registry
        WHERE verdict IS NULL OR verdict NOT IN ('KNOWN_THREAT', 'MALICIOUS')
        ORDER BY last_seen DESC
        """
        return ws_query(sql)
    except Exception as e:
        logger.error(f"Failed to get servers: {e}")
        return []


def correlate_server(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    server_id = server.get("server_id", "")
    url = server.get("url", "")
    name = server.get("name", "")

    all_checks = [
        ("world_articles", lambda: check_world_articles(server_id)),
    ]

    if url:
        all_checks.extend([
            ("phishtank", lambda: check_phishtank(url)),
        ])
        domain = extract_domain_from_url(url)
        if domain:
            all_checks.extend([
                ("urlvoid", lambda: check_urlvoid(domain)),
                ("maltiverse", lambda: check_maltiverse(domain)),
            ])
        ip = extract_ip_from_url(url)
        if ip:
            all_checks.append(("abuseipdb", lambda: check_abuseipdb(ip)))

    for check_name, check_func in all_checks:
        result = rate_limited_request(check_func)
        if result:
            result["check_source"] = check_name
            result["server_id"] = server_id
            result["server_name"] = name
            logger.warning(f"THREAT DETECTED for {server_id} from {check_name}: {result}")
            return result

    return None


def ensure_tables() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence    TEXT,
        severity    VARCHAR,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    return ws_execute(sql)


def run_correlation_cycle() -> Dict[str, int]:
    stats = {"checked": 0, "threats_found": 0, "updated": 0}
    
    ensure_tables()
    servers = get_servers_to_check()
    logger.info(f"Starting correlation check for {len(servers)} servers")

    for server in servers:
        server_id = server.get("server_id", "")
        if not server_id:
            continue

        stats["checked"] += 1
        threat = correlate_server(server)

        if threat:
            stats["threats_found"] += 1
            if record_threat_association(server_id, threat):
                stats["updated"] += 1
                update_server_verdict(
                    server_id,
                    "KNOWN_THREAT",
                    f"Cross-registry correlation: {threat.get('evidence', 'Threat detected')}"
                )

    logger.info(f"Correlation cycle complete: {stats}")
    return stats


def run():
    if not check_single_instance():
        logger.error("Another instance is running. Exiting.")
        return

    logger.info(f"Starting {SERVICE_NAME}")
    send_heartbeat()

    try:
        while True:
            try:
                run_correlation_cycle()
            except Exception as e:
                logger.error(f"Correlation cycle failed: {e}")

            send_heartbeat()
            logger.info(f"Sleeping for {POLL_INTERVAL}s until next correlation cycle")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        remove_pid_file()
        logger.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()