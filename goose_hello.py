#!/usr/bin/env python3
"""
ZO-SENTINEL Health Check Script
Queries the MCP server registry count from the write service.
"""
import json
import urllib.request
import urllib.error

QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
SQL_QUERY = "SELECT COUNT(*) as n FROM mcp_server_registry"


def query_registry_count():
    """Query the registry count from the write service."""
    payload = json.dumps({"sql": SQL_QUERY}).encode("utf-8")
    req = urllib.request.Request(
        QUERY_SERVICE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result
    except urllib.error.URLError as e:
        return {"error": str(e)}


def main():
    print("ZO-SENTINEL Registry Health Check")
    print("-" * 40)
    result = query_registry_count()
    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"Registry count query result: {result}")


if __name__ == "__main__":
    main()