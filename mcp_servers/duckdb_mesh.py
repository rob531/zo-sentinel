#!/usr/bin/env python3
"""MCP server: exposes DuckDB mesh queries to Goose Architect."""
import asyncio, json, requests
from mcp.server import Server
from mcp.server.stdio import stdio_server

QUERY_URL = "http://127.0.0.1:8772/query"
app = Server("duckdb_mesh")

@app.tool()
async def query_mesh(sql: str) -> str:
    """Run a read-only SQL query against the ZoSentinel DuckDB mesh."""
    try:
        r = requests.post(QUERY_URL, json={"sql": sql}, timeout=15)
        r.raise_for_status()
        rows = r.json().get("rows", r.json())
        return json.dumps(rows[:50], indent=2)  # cap at 50 rows
    except Exception as e:
        return f"ERROR: {e}"

@app.tool()
async def get_signal_quality() -> str:
    """Returns current signal discrimination stats - use to diagnose enricher quality."""
    sql = """
        SELECT signal_name,
               COUNT(*) as total,
               COUNT(DISTINCT ROUND(score,0)) as distinct_scores,
               ROUND(AVG(score),2) as avg_score,
               SUM(CASE WHEN score >= 95 THEN 1 ELSE 0 END)*100.0/COUNT(*) as pct_near_perfect
        FROM mcp_signal_scores
        GROUP BY signal_name ORDER BY distinct_scores ASC
    """
    try:
        r = requests.post(QUERY_URL, json={"sql": sql}, timeout=15)
        r.raise_for_status()
        return json.dumps(r.json(), indent=2)
    except Exception as e:
        return f"ERROR: {e}"

if __name__ == "__main__":
    asyncio.run(stdio_server(app))
