import os
import sys
import time
import signal
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import requests

# Constants
SERVICE_NAME = "temporal_stability_enrichment_integration"
PORT = None  # Not an HTTP service, daemon only
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
POLL_SECS = 60

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_single_instance():
    """Ensure only one instance runs via PID file."""
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance is running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except OSError:
            logger.info(f"Stale PID file found, removing.")
    pid_file.write_text(str(os.getpid()))


def remove_pid_file():
    """Clean up PID file on exit."""
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT gracefully."""
    logger.info(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: list) -> bool:
    """Write rows to DuckDB via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"write_service error for {table}: {e}")
        return False


def ws_query(sql: str) -> Optional[list]:
    """Query DuckDB via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=60
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except Exception as e:
        logger.error(f"Query error: {e}")
        return None


def send_heartbeat():
    """Write heartbeat to service_health."""
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": now,
        "status": "running",
        "meta": f"temporal_stability enrichment at {now}"
    }])


def get_unscored_servers(limit: int = 100) -> list:
    """Fetch MCPs lacking temporal_stability signal."""
    sql = f"""
    SELECT r.server_id, r.name, r.url, r.registry_source,
           r.first_seen, r.last_seen, r.trust_score, r.verdict,
           r.metadata
    FROM mcp_server_registry r
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments e
        WHERE e.server_id = r.server_id
        AND e.signal_type = 'temporal_stability'
    )
    AND r.verdict != 'unknown'
    LIMIT {limit}
    """
    return ws_query(sql) or []


def compute_temporal_stability(metadata: dict, first_seen: str, last_seen: str, url: str) -> dict:
    """Compute temporal stability score for an MCP.

    Target: move from 4 distinct values toward 20+ distinct values.
    Uses multiple signals for fine-grained scoring.
    """
    from temporal_stability_enrichment import compute_score as ts_compute

    # Build enriched metadata for compute_score
    enriched_meta = {
        **(metadata or {}),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "url": url
    }

    return ts_compute(enriched_meta)


def enrich_server(server: dict) -> Optional[dict]:
    """Enrich a single server with temporal stability score."""
    server_id = server.get("server_id")
    name = server.get("name")
    metadata = server.get("metadata", {})

    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    first_seen = server.get("first_seen", "")
    last_seen = server.get("last_seen", "")
    url = server.get("url", "")

    try:
        score_data = compute_temporal_stability(metadata, first_seen, last_seen, url)

        if not score_data:
            logger.warning(f"No score computed for {server_id}")
            return None

        # Compute deterministic ID from server_id + signal_type
        signal_type = "temporal_stability"
        id_source = f"{server_id}:{signal_type}"
        record_id = hashlib.sha256(id_source.encode()).hexdigest()[:32]

        now = datetime.now(timezone.utc).isoformat()

        return {
            "server_id": server_id,
            "signal_type": signal_type,
            "score_value": score_data.get("score", 0),
            "score_band": score_data.get("band", "unknown"),
            "confidence": score_data.get("confidence", 0.0),
            "evidence_blob": str(score_data.get("evidence", {})),
            "computed_at": now,
            "record_id": record_id
        }

    except Exception as e:
        logger.error(f"Error enriching server {server_id}: {e}")
        return None


def run_enrichment_cycle():
    """Process one batch of unscored servers."""
    logger.info("Starting temporal stability enrichment cycle")

    servers = get_unscored_servers(limit=100)
    if not servers:
        logger.info("No unscored servers found, skipping cycle")
        return

    logger.info(f"Found {len(servers)} servers to enrich")

    enriched = []
    for server in servers:
        result = enrich_server(server)
        if result:
            enriched.append(result)

    if enriched:
        # Upsert into mcp_signal_enrichments
        # Use ON CONFLICT DO UPDATE for DuckDB
        for row in enriched:
            upsert_sql = f"""
            INSERT INTO mcp_signal_enrichments
            (server_id, signal_type, score_value, score_band, confidence, evidence_blob, computed_at, record_id)
            VALUES (
                '{row['server_id']}',
                '{row['signal_type']}',
                {row['score_value']},
                '{row['score_band']}',
                {row['confidence']},
                '{row['evidence_blob'].replace("'", "''")}',
                '{row['computed_at']}',
                '{row['record_id']}'
            )
            ON CONFLICT DO UPDATE
            SET score_value = excluded.score_value,
                score_band = excluded.score_band,
                confidence = excluded.confidence,
                evidence_blob = excluded.evidence_blob,
                computed_at = excluded.computed_at
            """
            try:
                resp = requests.post(
                    WRITE_SERVICE_URL + "/execute",
                    json={"sql": upsert_sql},
                    timeout=30
                )
                if resp.status_code not in (200, 201):
                    logger.error(f"Upsert failed for {row['server_id']}: {resp.text}")
            except Exception as e:
                logger.error(f"Upsert error for {row['server_id']}: {e}")

        logger.info(f"Enriched {len(enriched)} servers with temporal_stability signal")
    else:
        logger.warning("No servers were successfully enriched this cycle")


def run():
    """Main daemon loop."""
    check_single_instance()

    # Install signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info(f"Starting {SERVICE_NAME}")

    # Initial heartbeat
    send_heartbeat()

    while True:
        try:
            run_enrichment_cycle()
            send_heartbeat()
        except Exception as e:
            logger.error(f"Cycle error: {e}")
            # Still send heartbeat even on failure
            send_heartbeat()

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()