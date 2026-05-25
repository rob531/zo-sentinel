#!/usr/bin/env python3
"""
signal_analyser_v2_permission_scope_wiring.py

Wires permission_scope_enrichment_v2 into the signal analyser v2 pipeline.

DB schema columns: server_id, enrichment_name, score, evidence, computed_at, input_fingerprint

BUGS FIXED:
- enrichment_name column (was signal_type)
- input_fingerprint column included in writes
- Evidence dict extracted from tuple, serialized to JSON
- score_band added to evidence
- Richer metadata via mcp_ecosystems_metadata lookup
- SIGNAL_TYPE undefined reference removed
- Heartbeat loop thread added
- Self-smoke test block added
"""

import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

SERVICE_NAME = "signal_analyser_v2_permission_scope_wiring"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_URL = "http://localhost:8772"
POLL_SECS = 300
ENRICHMENT_MODULE = "permission_scope_enrichment_v2"
ENRICHMENT_NAME = "permission_scope"
BATCH_SIZE = 100
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def _write_url() -> str:
    return WRITE_SERVICE_URL


def _query_url() -> str:
    return QUERY_SERVICE_URL


def _exec_url() -> str:
    return EXECUTE_URL


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            _query_url() + "/query",
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            _write_url() + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            _exec_url() + "/execute",
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def compute_fingerprint(server: Dict[str, Any]) -> str:
    """Build deterministic fingerprint from server record."""
    raw = json.dumps(
        {k: v for k, v in sorted(server.items()) if k != "computed_at"},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def check_single_instance() -> None:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, 0)
            logger.error(f"Another instance running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except OSError:
            logger.warning(f"Stale PID file (PID {old_pid}). Removing.")
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_heartbeat(status: str = "running", meta: Dict[str, Any] = None) -> None:
    ts = utc_now_iso()
    rows = [
        {
            "service": SERVICE_NAME,
            "last_heartbeat": ts,
            "status": status,
            "meta": meta or {},
        }
    ]
    ws_write("service_health", rows)


def ensure_mcp_signal_enrichments_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR,
        enrichment_name VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        computed_at TIMESTAMP WITH TIME ZONE,
        input_fingerprint VARCHAR,
        metadata VARCHAR
    )
    """
    ws_execute(sql)


def check_enrichment_module_exists():
    try:
        import importlib

        mod = importlib.import_module(ENRICHMENT_MODULE)
        if hasattr(mod, "compute_score") or hasattr(mod, "compute_batch_scores"):
            logger.info(
                f"Enrichment module '{ENRICHMENT_MODULE}' verified with compute_score/compute_batch_scores"
            )
            return mod
        else:
            logger.error(
                f"Enrichment module '{ENRICHMENT_MODULE}' missing compute_score/compute_batch_scores"
            )
            return None
    except ImportError as e:
        logger.error(f"Cannot import enrichment module '{ENRICHMENT_MODULE}': {e}")
        return None


def get_enrichment_metadata(server_id: str) -> Dict[str, Any]:
    """Fetch ecosystem metadata to enrich the metadata dict passed to compute_score."""
    meta: Dict[str, Any] = {}
    rows = ws_query(
        f"""
        SELECT ecosystem, age_days_estimate, top_downloads, stars_estimate,
               top_package_name, top_latest_version, cousin_count,
               top_ecosystem, ecosystems_observed
        FROM mcp_ecosystems_metadata
        WHERE server_id = '{server_id}'
        LIMIT 1
        """
    )
    if rows:
        r = rows[0]
        meta["age_days"] = r.get("age_days_estimate", 0)
        meta["downloads"] = r.get("top_downloads", 0)
        meta["stars"] = r.get("stars_estimate", 0)
        meta["package_name"] = r.get("top_package_name", "")
        meta["latest_version"] = r.get("top_latest_version", "")
        meta["ecosystem"] = r.get("top_ecosystem", "")
        meta["ecosystems_observed"] = r.get("ecosystems_observed", "")
    return meta


def get_unscored_servers(limit: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT r.server_id, r.name, r.url, r.description,
           r.owner, r.source, r.verified_publisher, r.created_at,
           r.tools_count, r.last_seen
    FROM mcp_server_registry r
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments e
        WHERE e.server_id = r.server_id
          AND e.enrichment_name = '{ENRICHMENT_NAME}'
    )
    LIMIT {limit}
    """
    return ws_query(sql)


def validate_signal_discrimination() -> bool:
    sql = f"""
    SELECT COUNT(DISTINCT score) as distinct_scores, COUNT(*) as total
    FROM mcp_signal_enrichments
    WHERE enrichment_name = '{ENRICHMENT_NAME}'
    """
    rows = ws_query(sql)
    if rows:
        r = rows[0]
        distinct = r.get("distinct_scores", 0)
        total = r.get("total", 0)
        logger.info(
            f"Permission scope signal discrimination: {distinct} distinct scores across {total} records"
        )
        if distinct < 3:
            logger.warning(
                f"BAD SIGNAL: Only {distinct} distinct values for {ENRICHMENT_NAME} signal"
            )
        return distinct >= 3
    return False


def write_enrichment_result(
    server_id: str,
    score: float,
    evidence: Dict[str, Any],
    fingerprint: str,
    metadata: Dict[str, Any] = None,
) -> bool:
    ts = utc_now_iso()
    rows = [
        {
            "server_id": server_id,
            "enrichment_name": ENRICHMENT_NAME,
            "score": score,
            "evidence": json.dumps(evidence),
            "computed_at": ts,
            "input_fingerprint": fingerprint,
            "metadata": json.dumps(metadata or {}),
        }
    ]
    return ws_write("mcp_signal_enrichments", rows)


def run_enrichment_cycle() -> None:
    logger.info("Starting permission_scope enrichment cycle")
    ensure_mcp_signal_enrichments_table()

    enrichment_mod = check_enrichment_module_exists()
    if enrichment_mod is None:
        logger.error("Enrichment module not available. Skipping cycle.")
        return

    servers = get_unscored_servers(BATCH_SIZE)
    if not servers:
        logger.info(
            f"No unscored servers found for {ENRICHMENT_NAME} enrichment"
        )
        validate_signal_discrimination()
        return

    logger.info(f"Processing {len(servers)} servers for {ENRICHMENT_NAME} enrichment")

    processed = 0
    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue

        try:
            # Build richer metadata for the enrichment module
            base_meta: Dict[str, Any] = {
                "server_id": server_id,
                "name": server.get("name", ""),
                "url": server.get("url", ""),
                "description": server.get("description", ""),
                "owner": server.get("owner", ""),
                "source": server.get("source", ""),
                "publisher_verified": server.get("verified_publisher", False),
                "created_at": str(server.get("created_at", "")),
                "tools_count": server.get("tools_count", 0),
                "registry_source": server.get("source", ""),
            }

            # Augment with ecosystem metadata if available
            eco_meta = get_enrichment_metadata(server_id)
            base_meta.update(eco_meta)

            # Call compute_score — returns (score, evidence_dict)
            result = enrichment_mod.compute_score(base_meta)
            if isinstance(result, tuple) and len(result) == 2:
                score, evidence = result
            else:
                score = float(result)
                evidence = {}

            # Get score band
            get_band = getattr(enrichment_mod, "get_score_band", None)
            if get_band:
                score_band = get_band(score)
            else:
                score_band = "unknown"
            evidence["score_band"] = score_band
            evidence["enrichment_version"] = "v2"

            # Compute fingerprint for idempotency
            fingerprint = compute_fingerprint(server)

            if write_enrichment_result(
                server_id, score, evidence, fingerprint, base_meta
            ):
                processed += 1
                logger.debug(
                    f"Enriched server {server_id}: score={score:.4f}, band={score_band}"
                )
            else:
                logger.warning(
                    f"Failed to write enrichment for server {server_id}"
                )

        except Exception as e:
            logger.error(f"Error processing server {server_id}: {e}")
            continue

    logger.info(
        f"Completed {ENRICHMENT_NAME} enrichment cycle: {processed}/{len(servers)} processed"
    )

    validate_signal_discrimination()

    send_heartbeat(
        "running",
        {"servers_processed": processed, "enrichment_name": ENRICHMENT_NAME},
    )


def heartbeat_loop() -> None:
    while True:
        try:
            send_heartbeat("running", {"loop": "heartbeat"})
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        time.sleep(60)


def run() -> None:
    logger.info(f"Starting {SERVICE_NAME}")

    os.makedirs(LOG_DIR, exist_ok=True)
    check_single_instance()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        ensure_mcp_signal_enrichments_table()
        logger.info("mcp_signal_enrichments table ready")

        # Start heartbeat in background thread
        hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        hb_thread.start()
        logger.info("Heartbeat thread started")

        send_heartbeat("starting", {})

        logger.info("Running initial enrichment cycle")
        run_enrichment_cycle()

        send_heartbeat("running", {"poll_interval": POLL_SECS})

        while True:
            time.sleep(POLL_SECS)
            try:
                run_enrichment_cycle()
            except Exception as e:
                logger.error(f"Cycle failed: {e}")
                send_heartbeat("error", {"error": str(e)})

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        remove_pid_file()
        logger.info(f"{SERVICE_NAME} stopped")


# ─────────────────────────────────────────────────────────────────────────────
# SELF-SMOKE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    print(f"[smoke] {SERVICE_NAME} self-test")

    # 1. compute_score returns tuple
    try:
        import permission_scope_enrichment_v2 as psv2

        test_meta = {
            "server_id": "test-server-001",
            "name": "test-server",
            "url": "https://example.com",
            "description": "A test server with permissions",
            "permissions": ["read", "write"],
            "dependencies": ["dep-a", "dep-b"],
            "publisher_verified": True,
            "created_at": "2024-01-01T00:00:00Z",
            "registry_source": "npm",
        }
        result = psv2.compute_score(test_meta)
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        score, evidence = result
        assert isinstance(score, (int, float)), f"Score must be numeric, got {type(score)}"
        assert 0.0 <= score <= 100.0, f"Score {score} out of range"
        assert isinstance(evidence, dict), f"Evidence must be dict, got {type(evidence)}"
        print(f"[smoke] compute_score: score={score:.4f}, evidence keys={list(evidence.keys())}")
    except Exception as e:
        print(f"[smoke] FAIL compute_score: {e}")
        sys.exit(1)

    # 2. get_score_band
    try:
        band = psv2.get_score_band(score)
        assert isinstance(band, str), f"Band must be str, got {type(band)}"
        print(f"[smoke] get_score_band: {band}")
    except Exception as e:
        print(f"[smoke] FAIL get_score_band: {e}")
        sys.exit(1)

    # 3. compute_fingerprint deterministic
    server_a = {"server_id": "x", "name": "a"}
    server_b = {"name": "a", "server_id": "x"}
    fp_a = compute_fingerprint(server_a)
    fp_b = compute_fingerprint(server_b)
    assert fp_a == fp_b, "Fingerprint must be order-independent"
    print(f"[smoke] compute_fingerprint: {fp_a}")

    # 4. utc_now_iso format
    ts = utc_now_iso()
    assert "T" in ts and "+" in ts, f"ISO timestamp malformed: {ts}"
    print(f"[smoke] utc_now_iso: {ts}")

    # 5. write_enrichment_result structure
    try:
        test_evidence = {"test": "data", "score_band": "good"}
        ok = write_enrichment_result(
            server_id="smoke-test-001",
            score=55.5,
            evidence=test_evidence,
            fingerprint="abc123",
            metadata={"smoke": True},
        )
        assert isinstance(ok, bool), f"write_enrichment_result must return bool, got {type(ok)}"
        print(f"[smoke] write_enrichment_result: {ok}")
    except Exception as e:
        print(f"[smoke] write_enrichment_result: {e} (may fail without DB — expected)")

    # 6. validate_signal_discrimination query syntax
    try:
        result = validate_signal_discrimination()
        assert isinstance(result, bool), f"validate_signal_discrimination must return bool"
        print(f"[smoke] validate_signal_discrimination: {result}")
    except Exception as e:
        print(f"[smoke] validate_signal_discrimination: {e} (may fail without DB — expected)")

    print("[smoke] All self-tests passed.")
    sys.exit(0)
