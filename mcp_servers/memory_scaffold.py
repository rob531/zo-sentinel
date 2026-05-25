#!/usr/bin/env python3
"""MCP server: syntax memory for Goose Architect - saves/recalls working configs."""
import asyncio, json, requests
from mcp.server import Server
from mcp.server.stdio import stdio_server

QUERY_URL = "http://127.0.0.1:8772/query"
WRITE_URL  = "http://127.0.0.1:8772/write"

app = Server("memory_scaffold")

@app.tool()
async def save_memory(key: str, value: str, context: str = "") -> str:
    """Save a working configuration or pattern to the syntax_memories table."""
    try:
        r = requests.post(WRITE_URL, json={
            "table": "syntax_memories",
            "rows": [{"memory_key": key, "memory_value": value,
                      "context": context, "created_at": "now()"}]
        }, timeout=10)
        return "saved" if r.status_code == 200 else f"error: {r.text[:100]}"
    except Exception as e:
        return f"ERROR: {e}"

@app.tool()
async def search_memory(query: str) -> str:
    """Search syntax_memories for prior working patterns."""
    try:
        r = requests.post(QUERY_URL, json={"sql": f"""
            SELECT memory_key, memory_value, context, created_at
            FROM syntax_memories
            WHERE memory_key ILIKE '%{query}%' OR context ILIKE '%{query}%'
            ORDER BY created_at DESC LIMIT 10
        """}, timeout=10)
        rows = r.json() if r.status_code == 200 else []
        return json.dumps(rows, indent=2) if rows else "No memories found"
    except Exception as e:
        return f"ERROR: {e}"

if __name__ == "__main__":
    asyncio.run(stdio_server(app))
