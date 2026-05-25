#!/usr/bin/env python3
"""
Patch write_service.py to add all sentinel tables with non-standard PKs
to _NO_AUTO_ID and _TABLE_PK so auto-id injection doesn't cause crashes.
"""
from pathlib import Path
import ast

path = Path('/home/workspace/zo_mesh/write_service.py')
content = path.read_text()

old = """_NO_AUTO_ID = ("write_queue_log", "service_health", "world_topics")

_TABLE_PK = {
    "service_health": "service",
    "world_topics":   "topic",
}"""

new = """_NO_AUTO_ID = (
    "write_queue_log", "service_health", "world_topics",
    # Sentinel tables with non-id primary keys
    "mcp_server_registry", "mcp_risk_register", "mcp_attestations",
    "mcp_submissions", "mcp_policy_rules", "mcp_tool_hashes",
    "mcp_fingerprints", "mcp_exemptions", "audit_log", "auth_tokens",
)

_TABLE_PK = {
    "service_health":      "service",
    "world_topics":        "topic",
    "mcp_server_registry": "server_id",
    "mcp_risk_register":   "server_id",
    "mcp_attestations":    "attestation_id",
    "mcp_submissions":     "submission_id",
    "mcp_tool_hashes":     "server_id",
    "mcp_fingerprints":    "server_id",
    "mcp_exemptions":      "exemption_id",
    "audit_log":           "event_id",
    "auth_tokens":         "token_id",
}"""

if old in content:
    content = content.replace(old, new)
    try:
        ast.parse(content)
        path.write_text(content)
        print('[OK] write_service.py patched -- PKs fixed')
    except SyntaxError as e:
        print(f'[!!] syntax error: {e}')
else:
    print('[--] pattern not found -- checking current state...')
    if 'mcp_server_registry' in content and '_NO_AUTO_ID' in content:
        print('    Already patched or different format')
    else:
        print('    Needs manual inspection')