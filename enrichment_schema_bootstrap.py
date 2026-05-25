#!/usr/bin/env python3
"""
enrichment_schema_bootstrap.py -- Creates mcp_signal_enrichments in main DuckDB.

Peer to full_schema_bootstrap.py. Run via python3 after the main bootstrap on
every boot. Idempotent.

Usage:
    python3 /home/workspace/zo_sentinel/enrichment_schema_bootstrap.py

Exit codes:
    0 -- table exists (created or verified)
    1 -- failure

Design doc: ENRICHMENT_STAGING.md
"""
import requests
import time
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger()

WS = "http://127.0.0.1:8772"


def wait_for_ws(max_wait: int = 30) -> bool:
    for i in range(max_wait):
        try:
            r = requests.get(WS + "/health", timeout=3)
            if r.status_code == 200:
                log.info("write_service ready after %ds", i)
                return True
        except Exception:
            pass
        time.sleep(1)
    log.error("write_service not ready after %ds", max_wait)
    return False


def ex(sql: str, label: str) -> bool:
    try:
        r = requests.post(
            WS + "/execute",
            json={"sql": sql.strip(), "wait": True},
            timeout=30,
        )
        if r.status_code == 200:
            log.info("[OK] %s", label)
            return True
        log.warning("[FAIL] %s: HTTP %s %s", label, r.status_code, r.text[:120])
        return False
    except Exception as e:
        log.error("[ERR] %s: %s", label, e)
        return False


# The single table this file owns. Note:
#   - id as BIGINT PRIMARY KEY with nextval, consistent with our other sentinel tables
#   - (run_id, enrichment_name, server_id) is UNIQUE so re-running a given run_id
#     against the same enrichment and server is idempotent (overwrites), not duplicative
#   - input_fingerprint is separate from evidence; fingerprint is a short hash of the
#     inputs used for sensitivity detection, evidence is human-readable JSON of what
#     fields were considered
SCHEMA = [
    "CREATE SEQUENCE IF NOT EXISTS seq_enrichment_id START 1",
    """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        id                 BIGINT PRIMARY KEY DEFAULT nextval('seq_enrichment_id'),
        run_id             VARCHAR NOT NULL,
        enrichment_name    VARCHAR NOT NULL,
        server_id          VARCHAR NOT NULL,
        score              FLOAT NOT NULL,
        evidence           TEXT,
        input_fingerprint  VARCHAR,
        computed_at        TIMESTAMPTZ DEFAULT now(),
        UNIQUE (run_id, enrichment_name, server_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_enrichments_name ON mcp_signal_enrichments(enrichment_name)",
    "CREATE INDEX IF NOT EXISTS idx_enrichments_run ON mcp_signal_enrichments(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_enrichments_server ON mcp_signal_enrichments(server_id)",
]


def main() -> int:
    if not wait_for_ws():
        log.error("Aborting -- write_service unavailable")
        return 1

    ok = sum(1 for stmt in SCHEMA if ex(stmt, stmt.strip().split("\n")[0][:60]))
    if ok < len(SCHEMA):
        log.error("%d of %d statements failed", len(SCHEMA) - ok, len(SCHEMA))
        return 1

    # Verify
    try:
        r = requests.post(
            WS + "/query",
            json={"sql": "SELECT COUNT(*) AS n FROM mcp_signal_enrichments"},
            timeout=10,
        )
        if r.status_code == 200:
            n = r.json().get("rows", [{}])[0].get("n", "?")
            log.info("mcp_signal_enrichments ready: %s rows", n)
            return 0
        log.error("Verification failed: HTTP %s", r.status_code)
        return 1
    except Exception as e:
        log.error("Verification error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())