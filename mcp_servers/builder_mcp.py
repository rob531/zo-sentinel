#!/usr/bin/env python3
"""
builder_mcp.py - FastMCP bridge: Goose -> ladder_shim -> ZoBuilder -> MiniMax
Single file. No imports of builder internals. Just a typed HTTP relay.
Gemini architecture: delegate_to_builder tool via FastMCP + httpx -> 8796
"""
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Zo Sentinel Builder Bridge")

SHIM_URL = "http://127.0.0.1:8796/v1/chat/completions"

@mcp.tool()
async def delegate_to_builder(target_file: str, strict_specification: str, context_type: str) -> str:
    """
    CRITICAL: The ONLY way to write or modify Python files.
    DO NOT write code yourself - always use this tool.
    Passes a strict spec to ZoBuilder via the ladder shim (MiniMax -> ZoBuilder).

    Args:
        target_file: Path to generate, relative to /home/workspace/zo_sentinel/
        strict_specification: Exact algorithmic logic, math, acceptance criteria.
        context_type: File type: 'enricher', 'daemon', 'schema', 'utility'
    """
    payload = {
        "model": "zo-ladder-v1",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are ZoBuilder. Generate a production-quality {context_type} file. "
                    f"Output ONLY the complete Python file content, no markdown, no explanation. "
                    f"Write to: {target_file}"
                )
            },
            {
                "role": "user",
                "content": f"Target: {target_file}\n\nSpecification:\n{strict_specification}"
            }
        ],
        "temperature": 0.2,
        "max_tokens": 8192
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(SHIM_URL, json=payload)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            # Write the file
            import os
            out = f"/home/workspace/zo_sentinel/{target_file}"
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w") as f:
                f.write(content)
            lines = content.count("\n")
            return f"SUCCESS: {target_file} written ({lines} lines). Preview:\n{content[:200]}"
    except Exception as e:
        return f"BUILDER_BRIDGE_ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def read_signal_quality() -> str:
    """Read current enricher discrimination stats from the live DB."""
    import requests as req
    try:
        r = req.post("http://127.0.0.1:8772/query", json={"sql": """
            SELECT signal_name,
                   COUNT(*) as total,
                   COUNT(DISTINCT ROUND(score,0)) as distinct_scores,
                   ROUND(AVG(score),2) as avg_score
            FROM mcp_signal_scores
            GROUP BY signal_name ORDER BY distinct_scores ASC
        """}, timeout=15)
        return r.text if r.status_code == 200 else f"DB error: {r.status_code}"
    except Exception as e:
        return f"ERROR: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")