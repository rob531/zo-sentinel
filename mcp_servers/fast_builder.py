#!/usr/bin/env python3
"""MCP server: delegates codegen to ZoBuilder via its mesh_memory queue."""
import asyncio, json, uuid, requests
from datetime import datetime, timezone
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server

WRITE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
OUTPUT_DIR = Path("/home/workspace/zo_sentinel/builder_outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = Server("fast_builder")

@app.tool()
async def generate(target_file: str, specification: str, context: str = "") -> str:
    """
    Submit a codegen task to ZoBuilder via its mesh_memory directive queue.
    Returns a ticket_id. Use check_result(ticket_id) to poll for completion.
    target_file: output path relative to /home/workspace/zo_sentinel/
    specification: precise description of what to build with acceptance criteria
    """
    ticket_id = f"goose_arch_{uuid.uuid4().hex[:8]}"
    directive_content = json.dumps({
        "key": ticket_id,
        "title": f"Architect-generated: {target_file}",
        "spec": specification,
        "output_file": target_file,
        "context": context,
        "complexity": "medium",
        "handler": "generate_file",
        "status": "pending"
    })
    try:
        r = requests.post(WRITE_URL, json={
            "table": "mesh_memory",
            "rows": [{
                "agent_id": "zo_sentinel.directive",
                "memory_type": "build_directive",
                "content": directive_content,
                "importance": 0.95,
                "created_at": datetime.now(timezone.utc).isoformat()
            }]
        }, timeout=15)
        if r.status_code == 200:
            return f"QUEUED ticket={ticket_id} target={target_file}. Use check_result('{ticket_id}') to verify."
        return f"QUEUE_ERROR: {r.status_code} {r.text[:100]}"
    except Exception as e:
        return f"ERROR: {e}"

@app.tool()
async def check_result(ticket_id: str) -> str:
    """Check if a builder task completed. Pass the ticket_id from generate()."""
    try:
        # Check mesh_events for SKILL_COMPLETE with this ticket
        r = requests.post(QUERY_URL, json={"sql": f"""
            SELECT event_type, payload, created_at FROM mesh_events
            WHERE payload ILIKE '%{ticket_id}%'
            ORDER BY created_at DESC LIMIT 3
        """}, timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if rows:
            return json.dumps(rows, indent=2)
        # Also check if output file exists
        return f"No completion event found yet for {ticket_id}. Builder may still be processing."
    except Exception as e:
        return f"ERROR: {e}"

if __name__ == "__main__":
    asyncio.run(stdio_server(app))
