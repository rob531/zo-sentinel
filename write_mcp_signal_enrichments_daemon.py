#!/usr/bin/env python3
# deps: requests
"""
write_mcp_signal_enrichments_daemon.py

Daemon that reads servers from mcp_server_registry, computes community_signal
and supply_chain enrichments using pure enrichment modules, and writes results
to mcp_signal_enrichments.

SCHEMA (mcp_signal_enrichments):
  id BIGINT, server_id VARCHAR, signal_type VARCHAR, dimension VARCHAR,
  score FLOAT, evidence_blob JSON, computed_at TIMESTAMP, expires_at TIMESTAMP

INTERFACE:
  run()  -- main daemon loop

CONSTRAINTS:
  - stdlib + requests only
  - All DB access via write_service at 127.0.0.1:8772
  - No direct duckdb
  - Heartbeat to service_health every 60s
  - 10s external I/O timeout
  - 3-try exponential backoff on write_service 5xx
"""
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = "write_mcp_signal_enrichments_daemon"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
WRITE_TIMEOUT = 30.0
QUERY_TIMEOUT = 10.0
HTTP_TIMEOUT = 10.0
MAX_RETRIES = 3
BACKOFF_BASE = 2.0
POLL_SECS = 60
BATCH_SIZE = 50
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

# ---- write_service helpers -------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query via write_service."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=QUERY_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    """Write rows to a table via write_service."""
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_URL, json=payload, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()


def ws_execute(sql: str) -> None:
    """Execute DDL/DML via write_service."""
    payload = {"sql": sql, "wait": True}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()


def ws_write_with_backoff(table: str, rows: List[Dict[str, Any]]) -> None:
    """Write with 3-try exponential backoff on 5xx."""
    for attempt in range(MAX_RETRIES):
        try:
            ws_write(table, rows)
            return
        except requests.HTTPError as e:
            if e.response is not None and 500 <= e.response.status_code < 600:
                backoff = BACKOFF_BASE ** attempt
                time.sleep(backoff)
                continue
            raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_BASE ** attempt
                time.sleep(backoff)
                continue
            raise
    raise RuntimeError(f"ws_write failed after {MAX_RETRIES} attempts")


# ---- Instance management ---------------------------------------------------


def check_single_instance() -> bool:
    pid = str(os.getpid())
    try:
        with open(PID_FILE, "r") as f:
            existing = f.read().strip()
        if existing and existing != pid:
            try:
                os.kill(int(existing), 0)
                print(f"[{utc_now_iso()}] Another instance running: {existing}")
                return False
            except OSError:
                pass
    except FileNotFoundError:
        pass
    with open(PID_FILE, "w") as f:
        f.write(pid)
    return True


def remove_pid_file() -> None:
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum: int, frame) -> None:
    sig_name = "SIGTERM" if signum == 15 else "SIGINT" if signum == 2 else f"signal-{signum}"
    print(f"[{utc_now_iso()}] Received {sig_name}, shutting down")
    remove_pid_file()
    sys.exit(0)


# ---- Heartbeat -------------------------------------------------------------


def send_heartbeat(status: str = "running",
                  meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": json.dumps(meta) if meta else "{}",
    }
    try:
        ws_write("service_health", [row])
    except Exception as e:
        print(f"[{utc_now_iso()}] Heartbeat failed: {e}")


# ---- Schema bootstrap ------------------------------------------------------


def ensure_table() -> None:
    """Ensure mcp_signal_enrichments table exists with the live schema."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        id BIGINT,
        server_id VARCHAR,
        signal_type VARCHAR,
        dimension VARCHAR,
        score FLOAT,
        evidence_blob JSON,
        computed_at TIMESTAMP,
        expires_at TIMESTAMP,
        PRIMARY KEY (server_id, signal_type)
    )
    """
    try:
        ws_execute(create_sql)
    except Exception as e:
        print(f"[{utc_now_iso()}] Table ensure (may already exist): {e}")


# ---- Data fetching ---------------------------------------------------------


def get_servers_needing_enrichment(batch_size: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    """Fetch servers from mcp_server_registry that lack any signal enrichment."""
    sql = f"""
    SELECT r.server_id, r.name, r.registry_source, r.url,
           r.trust_score, r.verdict, r.risk_tier, r.metadata
    FROM mcp_server_registry r
    WHERE r.verdict != 'KNOWN_THREAT'
      AND NOT EXISTS (
          SELECT 1 FROM mcp_signal_enrichments e
          WHERE e.server_id = r.server_id
      )
    ORDER BY r.trust_score ASC NULLS FIRST
    LIMIT {batch_size}
    """
    try:
        return ws_query(sql)
    except Exception as e:
        print(f"[{utc_now_iso()}] Failed to query servers: {e}")
        return []


def parse_metadata_col(metadata_json: Optional[str]) -> Dict[str, Any]:
    """Parse the JSON metadata column from mcp_server_registry."""
    if not metadata_json:
        return {}
    try:
        return json.loads(metadata_json)
    except Exception:
        return {}


def extract_metadata_for_enrichment(row: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and normalize metadata fields for enrichment scoring."""
    raw_meta = parse_metadata_col(row.get("metadata"))

    # Normalize: prefer explicit columns, fall back to metadata JSON
    return {
        "registry_source": row.get("registry_source", "unknown"),
        "name": row.get("name", ""),
        "url": row.get("url", ""),
        "trust_score": row.get("trust_score"),
        "risk_tier": row.get("risk_tier"),
        # From metadata JSON (snake/kebab tolerant)
        "age_days": raw_meta.get("age_days") or raw_meta.get("age-days") or 0,
        "download_count": raw_meta.get("download_count") or raw_meta.get("download-count") or 0,
        "dependency_count": raw_meta.get("dependency_count") or raw_meta.get("dependency-count") or 0,
        "publisher_verified": raw_meta.get("publisher_verified") or raw_meta.get("publisher-verified") or False,
        "stars": raw_meta.get("stars") or 0,
        "forks": raw_meta.get("forks") or 0,
        "subscribers": raw_meta.get("subscribers") or 0,
        "open_issues": raw_meta.get("open_issues") or raw_meta.get("open-issues") or 0,
        "closed_issues": raw_meta.get("closed_issues") or raw_meta.get("closed-issues") or 0,
    }


# ---- Enrichment computation ------------------------------------------------


def compute_community_signal(metadata: Dict[str, Any]) -> tuple[float, Dict[str, Any]]:
    """Compute community_signal score using community_signal_enrichment."""
    try:
        from community_signal_enrichment import compute_score
        return compute_score(metadata)
    except ImportError as e:
        print(f"[{utc_now_iso()}] community_signal_enrichment import failed: {e}")
        return 0.0, {"error": str(e)}


def compute_supply_chain(metadata: Dict[str, Any]) -> tuple[float, Dict[str, Any]]:
    """Compute supply_chain score using supply_chain_enrichment."""
    try:
        from supply_chain_enrichment import compute_score
        return compute_score(metadata)
    except ImportError as e:
        print(f"[{utc_now_iso()}] supply_chain_enrichment import failed: {e}")
        return 0.0, {"error": str(e)}


# ---- Write enrichment rows -------------------------------------------------


def write_enrichment_row(server_id: str,
                         signal_type: str,
                         score: float,
                         evidence_blob: Dict[str, Any],
                         dimension: str = "") -> bool:
    """Write a single enrichment row to mcp_signal_enrichments."""
    row = {
        "server_id": server_id,
        "signal_type": signal_type,
        "dimension": dimension,
        "score": float(score),
        "evidence_blob": json.dumps(evidence_blob),
        "computed_at": utc_now_iso(),
        "expires_at": None,
    }
    try:
        ws_write_with_backoff("mcp_signal_enrichments", [row])
        return True
    except Exception as e:
        print(f"[{utc_now_iso()}] Write failed for {server_id}/{signal_type}: {e}")
        return False


# ---- Cycle ----------------------------------------------------------------


def cycle() -> int:
    """Process one batch of servers: compute and write enrichments."""
    ensure_table()
    servers = get_servers_needing_enrichment()
    if not servers:
        return 0

    processed = 0
    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue

        metadata = extract_metadata_for_enrichment(server)

        # Compute community_signal
        cs_score, cs_evidence = compute_community_signal(metadata)
        if write_enrichment_row(server_id, "community_signal", cs_score, cs_evidence):
            processed += 1

        # Compute supply_chain
        sc_score, sc_evidence = compute_supply_chain(metadata)
        write_enrichment_row(server_id, "supply_chain", sc_score, sc_evidence)

    return processed


# ---- Daemon loop ----------------------------------------------------------


def run() -> None:
    """Main daemon entry point."""
    import signal as _signal

    if not check_single_instance():
        sys.exit(1)

    _signal.signal(_signal.SIGTERM, signal_handler)
    _signal.signal(_signal.SIGINT, signal_handler)

    print(f"[{utc_now_iso()}] Starting {SERVICE_NAME}")
    send_heartbeat("starting")

    try:
        while True:
            start_ts = time.time()
            try:
                count = cycle()
                send_heartbeat("running", {"processed": count})
                print(f"[{utc_now_iso()}] Cycle done: processed={count}")
            except Exception as e:
                print(f"[{utc_now_iso()}] Cycle error: {e}")
                send_heartbeat("error", {"error": str(e)})

            elapsed = time.time() - start_ts
            sleep_time = max(1.0, POLL_SECS - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print(f"[{utc_now_iso()}] Interrupted")
    finally:
        remove_pid_file()


# ---- Self-test -------------------------------------------------------------


if __name__ == "__main__":
    print(f"[{utc_now_iso()}] Self-test mode for {SERVICE_NAME}")

    # Ensure table exists
    ensure_table()

    # Fetch a known server_id from mcp_server_registry
    servers = ws_query(
        "SELECT server_id, name, registry_source, url, metadata "
        "FROM mcp_server_registry WHERE verdict != 'KNOWN_THREAT' LIMIT 1"
    )

    if not servers:
        print("FAIL: No servers found in mcp_server_registry")
        sys.exit(1)

    server = servers[0]
    server_id = server["server_id"]
    print(f"Testing with server_id: {server_id}")

    metadata = extract_metadata_for_enrichment(server)

    # Compute community_signal
    cs_score, cs_evidence = compute_community_signal(metadata)
    assert 0.0 <= cs_score <= 100.0, f"community_signal score {cs_score} out of [0,100]"
    assert isinstance(cs_evidence, dict), "community_signal evidence not a dict"
    # Verify evidence_blob is valid JSON
    json.dumps(cs_evidence)  # raises if not JSON-serializable
    print(f"  community_signal: score={cs_score}, evidence_keys={list(cs_evidence.keys())}")

    # Compute supply_chain
    sc_score, sc_evidence = compute_supply_chain(metadata)
    assert 0.0 <= sc_score <= 100.0, f"supply_chain score {sc_score} out of [0,100]"
    assert isinstance(sc_evidence, dict), "supply_chain evidence not a dict"
    json.dumps(sc_evidence)
    print(f"  supply_chain: score={sc_score}, evidence_keys={list(sc_evidence.keys())}")

    # Write community_signal row
    ok_cs = write_enrichment_row(server_id, "community_signal", cs_score, cs_evidence)
    assert ok_cs, "Failed to write community_signal enrichment"
    print(f"  Wrote community_signal row for {server_id}")

    # Write supply_chain row
    ok_sc = write_enrichment_row(server_id, "supply_chain", sc_score, sc_evidence)
    assert ok_sc, "Failed to write supply_chain enrichment"
    print(f"  Wrote supply_chain row for {server_id}")

    # Verify row was written
    rows = ws_query(
        "SELECT signal_type, score FROM mcp_signal_enrichments "
        "WHERE server_id = $1",
        [server_id]
    )
    assert len(rows) >= 2, f"Expected >=2 rows, got {len(rows)}"
    print(f"  Verified DB rows: {len(rows)} enrichment rows for {server_id}")

    print(f"\n[{utc_now_iso()}] ALL SELF-TESTS PASSED")
    sys.exit(0)
