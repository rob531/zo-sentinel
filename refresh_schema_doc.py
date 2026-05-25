#!/usr/bin/env python3
"""
refresh_schema_doc.py
Queries information_schema from the live DuckDB via write_service
and rewrites DB_SCHEMA.md with the ground truth.
Run any time tables change: python3 refresh_schema_doc.py
"""
import requests
from datetime import datetime, timezone
from pathlib import Path

WRITE_SERVICE = "http://127.0.0.1:8772"
OUT = Path("/home/workspace/zo_sentinel/DB_SCHEMA.md")
SKIP_TABLES = {'agent_outputs','agent_runs','corrections','inference_log',
               'write_queue_log','perf_metrics','world_articles','world_topics'}

rows = requests.post(f"{WRITE_SERVICE}/query", json={"sql":
    "SELECT table_name, column_name, data_type "
    "FROM information_schema.columns WHERE table_schema='main' "
    "ORDER BY table_name, ordinal_position"}, timeout=10).json().get("rows", [])

tables = {}
for r in rows:
    t = r["table_name"]
    if t not in SKIP_TABLES:
        tables.setdefault(t, []).append((r["column_name"], r["data_type"].replace(" WITH TIME ZONE","TZ")))

lines = [
    "# ZO-SENTINEL DuckDB Schema",
    f"# AUTO-GENERATED {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    "# Regenerate: python3 /home/workspace/zo_sentinel/refresh_schema_doc.py",
    ""
]
for table, cols in sorted(tables.items()):
    lines.append(f"## {table}")
    lines.append("| Column | Type |")
    lines.append("|--------|------|")
    for col, dtype in cols:
        lines.append(f"| {col} | {dtype} |")
    lines.append("")

lines += [
    "## Common Mistakes",
    "- audit_log: `timestamp` not `created_at`; `target_server_id` not `server_id`",
    "- mcp_submissions: `requested_by` not `requester_name`; `mcp_name` not `mcp_identifier`",
    "- mcp_risk_register: `computed_at` not `last_assessed`",
    "- mcp_policy_rules: use `rule_type`+`pattern`, no condition_field/condition_operator",
    "- service_health: has `status` and `meta` columns",
]

OUT.write_text("\n".join(lines) + "\n")
print(f"Written {OUT} ({len(rows)} columns across {len(tables)} tables)")