#!/usr/bin/env python3
"""
mcp_signal_enrichments_schema.py -- Schema definition for mcp_signal_enrichments table.

This module defines the SQL schema for the enrichment-writes table used by
enrichment modules to write computed evidence (PRODUCT_SPEC §3).

Pure schema definition — no functions, no daemon, no network, no DB writes at runtime.
File exports SCHEMA_SQL constant for use by schema bootstrap utilities.
"""

# deps: requests

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
    id               BIGINT PRIMARY KEY,
    mcp_server_id    VARCHAR NOT NULL,
    enrichment_type VARCHAR NOT NULL,
    score            DOUBLE,
    evidence         JSON,
    computed_at      TIMESTAMPTZ DEFAULT now(),
    expires_at       TIMESTAMPTZ DEFAULT (now() + INTERVAL 30 days)
);

CREATE INDEX IF NOT EXISTS idx_enrichments_server_type
    ON mcp_signal_enrichments (mcp_server_id, enrichment_type);
"""


if __name__ == "__main__":
    if SCHEMA_SQL and "CREATE TABLE IF NOT EXISTS mcp_signal_enrichments" in SCHEMA_SQL:
        print("SCHEMA OK")
    else:
        print("SCHEMA ERROR: invalid SCHEMA_SQL")
