#!/usr/bin/env python3
"""
builder_mcp.py - FastMCP bridge: Goose -> ladder_shim -> ZoBuilder -> MiniMax
Single file. No imports of builder internals. Just a typed HTTP relay.
Gemini architecture: delegate_to_builder tool via FastMCP + httpx -> 8796
"""
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/home/workspace/zo_sentinel")  # for the zo_sentinel package
from zo_sentinel.build_routing import build_artifact_row  # noqa: E402

mcp = FastMCP("Zo Sentinel Builder Bridge")

SHIM_URL = "http://127.0.0.1:8796/v1/chat/completions"
WRITE_SERVICE = "http://127.0.0.1:8772"

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
    tier = os.environ.get("ZO_BUILD_TIER", "zo-ladder-v1")  # complexity-routed (#16)
    payload = {
        "model": tier,
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
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            content = _strip_code_fences(content)   # rungs add ```python despite "ONLY the file"
            # Write the file
            out = f"/home/workspace/zo_sentinel/{target_file}"
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w") as f:
                f.write(content)
            lines = content.count("\n")
            # Provenance from the shim (#16/#17): which ladder rung actually built it
            actual_tier = data.get("x_zo_task") or tier
            await _emit_build_artifact(client, target_file, content, context_type,
                                       actual_tier, data.get("x_zo_model", ""),
                                       data.get("x_zo_backend", ""))
            return (f"SUCCESS: {target_file} written ({lines} lines, tier={actual_tier}). "
                    f"Preview:\n{content[:200]}")
    except Exception as e:
        return f"BUILDER_BRIDGE_ERROR: {type(e).__name__}: {e}"


async def _emit_build_artifact(client, target_file, content, context_type,
                               tier, model, backend):
    """Emit a build_artifact mesh row so the ingestor / governor / publisher see
    the LIVE goose build (the legacy zo_sentinel_builder feed is frozen). Best-
    effort: a write_service hiccup must never fail the build itself."""
    row = build_artifact_row(
        file=target_file, content_bytes=len(content), context_type=context_type,
        tier=tier, model=model, backend=backend,
        phase=os.environ.get("ZO_BUILD_PHASE", ""),
        task=os.environ.get("ZO_BUILD_TASK", ""),
    )
    try:
        await client.post(f"{WRITE_SERVICE}/write",
                          json={"table": "mesh_memory", "rows": [row], "wait": True})
    except Exception:
        pass


@mcp.tool()
async def register_build(target_file: str, context_type: str) -> str:
    """Record a goose-built file as a build_artifact (provenance for the
    ingestor / governor / publisher). Call this ONCE, at the END of a build,
    ONLY after YOU (goose) wrote the file with the developer extension AND
    `python -m py_compile` passed.

    delegate_to_builder is the legacy single-shot path that writes + registers
    in one call; register_build is its counterpart for the Phase 1 flow where
    goose writes the file itself and verifies it before registering.

    Args:
        target_file: Path written, relative to /home/workspace/zo_sentinel/
        context_type: 'enricher', 'daemon', 'schema', 'utility'
    """
    out = f"/home/workspace/zo_sentinel/{target_file}"
    if not os.path.exists(out):
        return f"REGISTER_ERROR: {target_file} not on disk -- write it first."
    with open(out) as f:
        content = f.read()
    if len(content.strip()) < 32:
        return (f"REGISTER_ERROR: {target_file} is {len(content)}b -- too small to be a "
                "real build; do not register a stub.")
    tier = os.environ.get("ZO_BUILD_TIER", "zo-ladder-low")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _emit_build_artifact(client, target_file, content, context_type,
                                       tier, os.environ.get("GOOSE_MODEL", ""),
                                       "goose_developer")
        return (f"REGISTERED: {target_file} ({content.count(chr(10))} lines, "
                f"tier={tier}, backend=goose_developer)")
    except Exception as e:
        return f"REGISTER_ERROR: {type(e).__name__}: {e}"


def _strip_code_fences(text: str) -> str:
    """Remove a leading ```lang fence + trailing ``` some models add despite the
    'output ONLY the file' instruction -- writing them verbatim breaks py_compile."""
    s = text.strip()
    if not s.startswith("```"):
        return text
    lines = s.split("\n")
    lines = lines[1:]                                  # drop ```python / ```
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


@mcp.tool()
async def read_signal_quality() -> str:
    """Read current enricher discrimination stats from the live DB.

    Async httpx (NOT sync requests): a blocking call inside a FastMCP @tool
    stalls the event loop and the Goose subprocess times out (constraint #1)."""
    sql = """
            SELECT signal_name,
                   COUNT(*) as total,
                   COUNT(DISTINCT ROUND(score,0)) as distinct_scores,
                   ROUND(AVG(score),2) as avg_score
            FROM mcp_signal_scores
            GROUP BY signal_name ORDER BY distinct_scores ASC
        """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{WRITE_SERVICE}/query", json={"sql": sql})
            return r.text if r.status_code == 200 else f"DB error: {r.status_code}"
    except Exception as e:
        return f"ERROR: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")