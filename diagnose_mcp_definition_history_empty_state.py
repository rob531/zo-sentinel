# deps: requests
"""diagnose_mcp_definition_history_empty_state.py

Utility script to diagnose why the `mcp_definition_history` table is empty.
It queries the Sentinel write_service HTTP API (query endpoint) to gather
information about registered MCP servers and any related audit logs.

The script is safe to import – all work is performed inside ``run()`` and
guarded by ``if __name__ == '__main__':``.
"""

from __future__ import annotations

import json
import sys
from typing import Any, List, Mapping, Sequence

import requests

# Base URL for the write_service HTTP API
_BASE_URL = "http://127.0.0.1:8772"


def _post(endpoint: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """POST ``payload`` to ``endpoint`` and return the parsed JSON response.

    Raises ``RuntimeError`` on network errors or non‑200 responses.
    """
    url = f"{_BASE_URL}{endpoint}"
    try:
        resp = requests.post(url, json=payload, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"Failed to contact write_service at {url}: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"write_service returned {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from write_service: {exc}") from exc


def query(sql: str, params: Sequence[Any] | None = None) -> List[Mapping[str, Any]]:
    """Execute a SELECT query via the write_service ``/query`` endpoint.

    ``params`` are passed positionally; if omitted an empty list is used.
    Returns a list of row dictionaries.
    """
    if params is None:
        params = []
    payload = {"sql": sql, "params": list(params)}
    result = _post("/query", payload)
    # The service returns ``{"rows": [...], "columns": [...]}``
    return result.get("rows", [])


def _print_header(msg: str) -> None:
    print("=" * 80)
    print(msg)
    print("=" * 80)


def run() -> None:
    """Diagnose the empty state of ``mcp_definition_history``.

    The function prints a short report to stdout. It never writes to the DB.
    """
    try:
        # 1. List registered MCP servers
        servers = query(
            "SELECT server_id, server_name FROM mcp_server_registry ORDER BY server_id"
        )
        _print_header("MCP Server Registry")
        if not servers:
            print("No servers are registered in `mcp_server_registry`.")
        else:
            print(f"Found {len(servers)} registered server(s):")
            for srv in servers:
                print(f"  - id={srv.get('server_id')} name={srv.get('server_name')}")

        # 2. Check definition history count
        history_rows = query("SELECT COUNT(*) AS cnt FROM mcp_definition_history")
        history_cnt = history_rows[0].get("cnt") if history_rows else 0
        _print_header("mcp_definition_history Count")
        print(f"Total rows in `mcp_definition_history`: {history_cnt}")

        # 3. If empty, look for recent audit log entries related to the populator
        if history_cnt == 0:
            _print_header("Recent audit_log entries for mcp_definition_history")
            audit_rows = query(
                "SELECT timestamp, target_server_id, message "
                "FROM audit_log "
                "WHERE target_table = ? "
                "ORDER BY timestamp DESC LIMIT 10",
                ["mcp_definition_history"],
            )
            if not audit_rows:
                print("No audit_log entries found for `mcp_definition_history`.")
                print("Possible causes:")
                print("  • The populator daemon is not running.")
                print("  • The daemon failed to start or crashed early.")
                print("  • No servers are configured to generate definition history.")
            else:
                print("Recent audit entries (most recent first):")
                for row in audit_rows:
                    ts = row.get("timestamp")
                    srv = row.get("target_server_id")
                    msg = row.get("message")
                    print(f"  [{ts}] server_id={srv} – {msg}")
                print("If the messages indicate errors, investigate the daemon logs.")
        else:
            print("`mcp_definition_history` already contains data; no diagnosis needed.")

        # 4. Suggest next steps
        _print_header("Suggested next steps")
        if not servers:
            print("1. Verify that MCP servers are being registered correctly.")
        if history_cnt == 0:
            print("2. Ensure the `mcp_definition_history` populator daemon is running.")
            print("   • Check its service health via the `service_health` table.")
            print("   • Review its logs for startup errors.")
    except Exception as exc:
        print("Error during diagnosis:", exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
