#!/usr/bin/env python3
"""
Fix write_service _NO_AUTO_ID and _TABLE_PK for sequence-based sentinel tables.
These tables have no PK constraint so must use plain INSERT not upsert.
"""
from pathlib import Path
import ast

path = Path('/home/workspace/zo_mesh/write_service.py')
content = path.read_text()

old = '''_NO_AUTO_ID = (
    "write_queue_log", "service_health", "world_topics",
    # Sentinel tables with non-id primary keys
    "mcp_server_registry", "mcp_risk_register", "mcp_attestations",
    "mcp_submissions", "mcp_policy_rules", "mcp_tool_hashes",
    "mcp_fingerprints", "mcp_exemptions", "audit_log", "auth_tokens",
)'''

new = '''_NO_AUTO_ID = (
    "write_queue_log", "service_health", "world_topics",
    # Sentinel tables with non-id primary keys
    "mcp_server_registry", "mcp_risk_register", "mcp_attestations",
    "mcp_submissions", "mcp_policy_rules", "mcp_tool_hashes",
    "mcp_fingerprints", "mcp_exemptions", "audit_log", "auth_tokens",
    # Sentinel tables with sequence IDs but NO PRIMARY KEY constraint
    # Must use plain INSERT to avoid "no UNIQUE/PK constraints" error
    "mcp_signal_scores", "mcp_threat_associations", "mcp_decisions",
    "mcp_definition_history", "mcp_policy_rules", "shodan_results",
    "github_velocity", "npm_typosquat_alerts", "perf_metrics",
)'''

if old in content:
    content = content.replace(old, new)
    try:
        ast.parse(content)
        path.write_text(content)
        print('[OK] write_service.py patched -- sequence tables added to _NO_AUTO_ID')
    except SyntaxError as e:
        print(f'[!!] syntax error: {e}')
elif 'mcp_signal_scores' in content:
    print('[--] already patched')
else:
    # Different format -- try the v1.3 format
    old2 = '    "mcp_fingerprints", "mcp_exemptions", "audit_log", "auth_tokens",\n)'
    new2 = '    "mcp_fingerprints", "mcp_exemptions", "audit_log", "auth_tokens",\n    "mcp_signal_scores", "mcp_threat_associations", "mcp_decisions",\n    "mcp_definition_history", "shodan_results", "github_velocity",\n    "npm_typosquat_alerts", "perf_metrics",\n)'
    if old2 in content:
        content = content.replace(old2, new2)
        ast.parse(content)
        path.write_text(content)
        print('[OK] patched via fallback')
    else:
        print('[!!] pattern not found -- inspect write_service.py manually')