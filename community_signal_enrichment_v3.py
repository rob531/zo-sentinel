import sys
import time
import math
import hashlib
from typing import Dict, Any, List, Optional
import requests

SERVICE_NAME = "community_signal_enrichment_v3"
PORT = 8785
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 300

SIGNAL_NAME = "community_signal_v3"
MAX_SCORE = 100.0

WEIGHTS = {
    "download_count": 0.25,
    "stars": 0.20,
    "github_stars": 0.15,
    "dependency_count": 0.10,
    "publisher_verified": 0.15,
    "registry_source": 0.10,
    "age_days": 0.05,
}

REGISTRY_SOURCE_SCORES = {
    "npm": 70,
    "github": 80,
    "smith": 90,
    "smith_official": 95,
    "builtin": 100,
    "manual": 60,
    "community": 50,
    "unknown": 40,
}


def ws_query(sql: str) -> Dict[str, Any]:
    resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise Exception(f"Write failed: {data}")


def ws_execute(sql: str) -> None:
    resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()


def check_single_instance() -> None:
    import os
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = int(f.read().strip())
        try:
            os.kill(existing, 0)
            print(f"Another instance running: {existing}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file() -> None:
    import os
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    remove_pid_file()
    sys.exit(0)


def compute_score(server_data: Dict[str, Any]) -> Dict[str, Any]:
    raw = {
        "download_count": server_data.get("download_count") or 0,
        "stars": server_data.get("stars") or 0,
        "github_stars": server_data.get("github_stars") or 0,
        "dependency_count": server_data.get("dependency_count") or 0,
        "publisher_verified": server_data.get("publisher_verified", False),
        "registry_source": server_data.get("registry_source", "unknown"),
        "age_days": server_data.get("age_days") or 0,
    }

    evidence = {}

    log_downloads = 0.0
    if raw["download_count"] > 0:
        log_downloads = math.log1p(raw["download_count"])
        evidence["log_downloads"] = round(log_downloads, 4)

    sqrt_stars = 0.0
    total_stars = raw["stars"] + raw["github_stars"]
    if total_stars > 0:
        sqrt_stars = math.sqrt(total_stars)
        evidence["sqrt_stars"] = round(sqrt_stars, 4)

    sqrt_dependencies = 0.0
    if raw["dependency_count"] > 0:
        sqrt_dependencies = math.sqrt(raw["dependency_count"])
        evidence["sqrt_dependencies"] = round(sqrt_dependencies, 4)

    registry_score = REGISTRY_SOURCE_SCORES.get(raw["registry_source"], REGISTRY_SOURCE_SCORES["unknown"])
    evidence["registry_source"] = raw["registry_source"]
    evidence["registry_source_score"] = registry_score

    sqrt_age = 0.0
    if raw["age_days"] > 0:
        sqrt_age = math.sqrt(min(raw["age_days"], 3650))
        evidence["sqrt_age_days"] = round(sqrt_age, 4)

    verified_score = 100.0 if raw["publisher_verified"] else 50.0
    evidence["publisher_verified"] = raw["publisher_verified"]
    evidence["verified_score"] = verified_score

    evidence["raw_downloads"] = raw["download_count"]
    evidence["raw_stars"] = raw["stars"]
    evidence["raw_github_stars"] = raw["github_stars"]
    evidence["raw_dependencies"] = raw["dependency_count"]
    evidence["raw_age_days"] = raw["age_days"]

    score = 0.0
    components = {}

    components["download"] = log_downloads * WEIGHTS["download_count"] * 10
    components["stars"] = sqrt_stars * WEIGHTS["stars"] * 5
    components["github_stars"] = sqrt_stars * WEIGHTS["github_stars"] * 3
    components["dependencies"] = sqrt_dependencies * WEIGHTS["dependency_count"] * 8
    components["verified"] = verified_score * WEIGHTS["publisher_verified"]
    components["registry"] = registry_score * WEIGHTS["registry_source"]
    components["age"] = sqrt_age * WEIGHTS["age_days"] * 2

    score = sum(components.values())

    score = max(0.0, min(MAX_SCORE, score))

    score_bucket = int(score * 10)
    evidence["score_bucket_10"] = score_bucket

    score_bucket_5 = int(score * 2)
    evidence["score_bucket_5"] = score_bucket_5

    components["total"] = round(score, 2)
    evidence["components"] = {k: round(v, 2) for k, v in components.items()}

    evidence["weights_applied"] = WEIGHTS.copy()

    return {
        "score": round(score, 2),
        "signal_name": SIGNAL_NAME,
        "evidence": evidence,
        "raw_inputs": raw,
        "score_hash": hashlib.md5(f"{round(score, 2)}{server_data.get('server_id', '')}".encode()).hexdigest()[:12],
    }


def ensure_table() -> None:
    ws_execute(f"""
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        server_id VARCHAR,
        signal_name VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, signal_name)
    )
    """)


def get_servers_for_scoring(limit: int = 500) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        server_id,
        name,
        url,
        registry_source,
        download_count,
        stars,
        github_stars,
        dependency_count,
        publisher_verified,
        created_at,
        trust_score
    FROM mcp_server_registry
    WHERE url IS NOT NULL 
      AND url != ''
    ORDER BY trust_score ASC NULLS FIRST, scan_count ASC NULLS FIRST
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result.get("rows", [])


def compute_age_days(created_at: Optional[str]) -> int:
    if not created_at:
        return 0
    try:
        from datetime import datetime
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.now()
        delta = now - created
        return max(0, delta.days)
    except Exception:
        return 0


def process_server(server: Dict[str, Any]) -> Dict[str, Any]:
    server_id = server["server_id"]
    server["age_days"] = compute_age_days(server.get("created_at"))

    server["github_stars"] = server.get("github_stars") or 0

    result = compute_score(server)

    return {
        "server_id": server_id,
        "signal_name": result["signal_name"],
        "score": result["score"],
        "evidence": str(result["evidence"]),
    }


def send_heartbeat() -> None:
    try:
        ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": "CURRENT_TIMESTAMP"})
    except Exception as e:
        print(f"Heartbeat failed: {e}")


def run() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    check_single_instance()
    ensure_table()

    print(f"{SERVICE_NAME} starting...")
    start_time = time.time()
    cycle_count = 0

    while True:
        try:
            cycle_start = time.time()
            cycle_count += 1

            servers = get_servers_for_scoring(limit=500)
            print(f"Cycle {cycle_count}: Processing {len(servers)} servers")

            if servers:
                rows = []
                for server in servers:
                    try:
                        row = process_server(server)
                        rows.append(row)
                    except Exception as e:
                        print(f"Error processing server {server.get('server_id')}: {e}")

                if rows:
                    ws_write("mcp_signal_scores", rows)
                    print(f"Wrote {len(rows)} signal scores")

            elapsed = time.time() - start_time
            print(f"Uptime: {int(elapsed)}s | Cycle: {cycle_count} | Servers: {len(servers)}")

            send_heartbeat()

            cycle_duration = time.time() - cycle_start
            sleep_time = max(1, POLL_SECS - cycle_duration)
            time.sleep(sleep_time)

        except Exception as e:
            print(f"Cycle error: {e}")
            time.sleep(30)


def get_score_distribution() -> Dict[str, Any]:
    sql = f"""
    SELECT 
        COUNT(DISTINCT server_id) as total,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        STDDEV(score) as stddev_score,
        COUNT(DISTINCT FLOOR(score * 10)) as distinct_buckets
    FROM mcp_signal_scores
    WHERE signal_name = '{SIGNAL_NAME}'
    """
    result = ws_query(sql)
    return result.get("rows", [{}])[0] if result.get("rows") else {}


if __name__ == "__main__":
    run()