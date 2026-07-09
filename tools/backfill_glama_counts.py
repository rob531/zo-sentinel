#!/usr/bin/env python3
"""tools/backfill_glama_counts.py -- retire the fabricated Glama tool_count:0.

Glama's API returns an empty tools[] for every server it can't introspect, so
the ingestor stamped tool_count:0 on all 48,544 Glama rows (60% of the
registry). That 0 reads as "0 tools" but MEANS "unknown" -- the sap-mcp-server
(~13 tools) confusion, 2026-07-04. THE LINE: don't publish a fabricated value.

This backfill rewrites unverified fabricated zeros to null and records the
provenance of the correction. It ONLY touches rows that were never verified
(no tool_count_verified flag, tool_count in (0, missing)); a row that carries a
real verified count from the fixed ingestor is left alone. Idempotent.

DEFAULT DRY-RUN; pass --apply. DB reached via the fly proxy DSN (--dsn-file).
"""
import argparse
import datetime
import json
import re
import sys


def fix_meta(meta: dict) -> bool:
    """Null the unverified fabricated tool_count/env_var_count in-place.
    Returns True if the row changed. Pure -- unit-tested without a DB."""
    if not isinstance(meta, dict) or "glama_id" not in meta:
        return False
    if meta.get("tool_count_verified") or meta.get("env_var_count_verified"):
        return False  # already carries a real verified count from fixed ingestor
    changed = False
    for field in ("tool_count", "env_var_count"):
        if meta.get(field) == 0 or (field in meta and meta.get(field) is None
                                    and f"{field}_verified" not in meta):
            if meta.get(field) is not None:
                changed = True
            meta[field] = None
            meta[f"{field}_verified"] = False
    if changed or "tool_count_verified" not in meta:
        meta.setdefault("tool_count_verified", False)
        meta.setdefault("env_var_count_verified", False)
        meta["count_provenance"] = (
            "glama_api_returned_empty_tools; 0 was fabricated, not observed; "
            "nulled by backfill_glama_counts " +
            datetime.datetime.utcnow().date().isoformat())
        changed = True
    return changed


def main():
    import psycopg2
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn-file", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15432)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dsn = open(a.dsn_file).read().strip()
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn)
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=m.group(3),
                            user=m.group(1), password=m.group(2))
    cur = conn.cursor()
    upd = conn.cursor()
    cur.execute("select server_id, metadata from mcp_server_registry "
                "where metadata like '%glama_id%'")
    checked = changed = 0
    for sid, raw in cur.fetchall():
        checked += 1
        try:
            meta = json.loads(raw)
        except Exception:
            continue
        if fix_meta(meta):
            changed += 1
            if a.apply:
                upd.execute("update mcp_server_registry set metadata=%s "
                            "where server_id=%s", (json.dumps(meta), sid))
    if a.apply:
        conn.commit()
    print(f"[glama-backfill] {'APPLIED' if a.apply else 'DRY-RUN'} "
          f"checked={checked} changed={changed}")
    cur.execute("select count(*) from mcp_server_registry "
                "where metadata like '%\"tool_count\": 0%'")
    print("[glama-backfill] fabricated tool_count:0 remaining:", cur.fetchone()[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
