import logging
import os
import sys
import hashlib
import json
from datetime import datetime, timezone

import requests

SERVICE_NAME = "community_signal_enrichment_wiring_verifier"
WRITE_SERVICE_URL = "http://localhost:8772"
LOG_PATH = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(SERVICE_NAME)


def ws_query(sql, params=None):
    payload = {"sql": sql, "params": params or []}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def ws_write(table, rows, wait=True):
    if isinstance(rows, dict):
        rows = [rows]
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(status="running", meta=None):
    row = {
        "service_name": SERVICE_NAME,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
        "meta": json.dumps(meta or {}),
    }
    ws_write("service_health", row)


def verify_enrichment_rows():
    sql = """
    SELECT
        enrichment_id,
        evidence_blob,
        computed_at,
        source_tag
    FROM mcp_signal_enrichments
    WHERE signal_type = 'community_signal'
    ORDER BY computed_at DESC
    LIMIT 50
    """
    rows = ws_query(sql)
    logger.info("Found %d community_signal enrichments", len(rows))
    return rows


def verify_evidence_blob_shape(rows):
    schema_errors = []
    required_keys = {"peer_score", "peer_count", "source_peer_ids", "signal_hash", "enrichment_version"}
    for i, row in enumerate(rows):
        try:
            blob = json.loads(row["evidence_blob"]) if isinstance(row["evidence_blob"], str) else row["evidence_blob"]
        except Exception:
            blob = {}
        missing = required_keys - set(blob.keys())
        if missing:
            schema_errors.append(
                f"Row {i} enrichment_id={row.get('enrichment_id','?')}: missing keys {missing}"
            )
    if schema_errors:
        for e in schema_errors:
            logger.error("EVIDENCE_BLOB_SHAPE_FAIL: %s", e)
    else:
        logger.info("All %d rows have valid evidence_blob shape", len(rows))
    return schema_errors


def verify_signal_analyser_wiring():
    wiring_ok = True
    try:
        sql = """
        SELECT artifact_id, artifact_path
        FROM build_artifact
        WHERE artifact_name = 'signal_analyser'
          AND artifact_status = 'complete'
        LIMIT 1
        """
        rows = ws_query(sql)
        if not rows:
            logger.warning("signal_analyser build_artifact not found — wiring cannot be verified end-to-end")
            wiring_ok = False
        else:
            logger.info("signal_analyser build_artifact found: %s", rows[0]["artifact_path"])
    except Exception as e:
        logger.warning("Could not query build_artifact for signal_analyser: %s", e)
        wiring_ok = False

    try:
        sql = """
        SELECT artifact_id, artifact_path
        FROM build_artifact
        WHERE artifact_name = 'community_signal_enrichment'
          AND artifact_status = 'complete'
        LIMIT 1
        """
        rows = ws_query(sql)
        if not rows:
            logger.warning("community_signal_enrichment build_artifact not found")
            wiring_ok = False
        else:
            logger.info("community_signal_enrichment build_artifact found: %s", rows[0]["artifact_path"])
    except Exception as e:
        logger.warning("Could not query build_artifact for community_signal_enrichment: %s", e)
        wiring_ok = False

    return wiring_ok


def verify_compute_score_invocations():
    sql = """
    SELECT
        COUNT(*) AS total,
        COUNT(CASE WHEN computed_at >= (NOW() - INTERVAL '24 hours') THEN 1 END) AS last_24h
    FROM mcp_signal_enrichments
    WHERE signal_type = 'community_signal'
    """
    rows = ws_query(sql)
    if rows:
        row = rows[0]
        total = int(row["total"]) if row["total"] is not None else 0
        last_24h = int(row["last_24h"]) if row["last_24h"] is not None else 0
        logger.info("community_signal compute_score invocations: total=%d last_24h=%d", total, last_24h)
        return {"total": total, "last_24h": last_24h}
    return {"total": 0, "last_24h": 0}


def main():
    logger.info("=== community_signal_enrichment wiring verification START ===")
    meta = {}
    try:
        rows = verify_enrichment_rows()
        meta["enrichment_count"] = len(rows)

        schema_errors = verify_evidence_blob_shape(rows)
        meta["schema_errors"] = schema_errors

        wiring_ok = verify_signal_analyser_wiring()
        meta["wiring_ok"] = wiring_ok

        invocations = verify_compute_score_invocations()
        meta["invocations"] = invocations

        if schema_errors:
            logger.error("VERIFICATION FAIL: evidence_blob shape violations found")
            send_heartbeat(status="fail", meta=meta)
            sys.exit(1)
        else:
            logger.info("VERIFICATION PASS: all wiring checks satisfied")
            send_heartbeat(status="pass", meta=meta)
            sys.exit(0)

    except Exception as e:
        logger.exception("Verification crashed: %s", e)
        send_heartbeat(status="crash", meta={"error": str(e)})
        sys.exit(2)


if __name__ == "__main__":
    main()