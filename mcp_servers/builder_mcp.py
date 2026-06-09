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
from minimax_utils import strip_code_fences  # noqa: E402  (canonical LLM sanitizer)

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
    'output ONLY the file' instruction. Delegates to the canonical sanitizer
    (minimax_utils) so the bridge, ladder, and generator strip identically."""
    return strip_code_fences(text)


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


async def _gquery(client, sql, params=None):
    """POST a read to write_service /query. Returns the rows list, or None if the
    query failed (e.g. code_nodes not seeded yet -> 400)."""
    r = await client.post(f"{WRITE_SERVICE}/query",
                          json={"sql": sql, "params": params or [], "limit": 60})
    if r.status_code != 200:
        return None
    return r.json().get("rows", [])


_GRAPH_RELS = "('calls','imports','imports_from','uses','inherits','references')"


@mcp.tool()
async def graph_neighbors(target: str) -> str:
    """Code-graph neighborhood of a file or symbol: what it DEPENDS ON (you
    call/import these -- keep their signatures) and what DEPENDS ON IT (these
    break if you change a contract). Query this BEFORE writing so your change
    respects the existing call/import structure.

    Reads the DuckDB code graph (code_nodes/code_edges) through write_service.
    If the graph isn't seeded yet it says so -- just proceed without it.

    Args:
        target: a file name/path fragment or a symbol/label
                (e.g. 'builder_mcp.py' or 'delegate_to_builder').
    """
    deps_sql = (
        "SELECT DISTINCT e.relation AS rel, n2.label AS name, n2.source_file AS file "
        "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
        "WHERE (n1.source_file LIKE ? OR n1.norm_label LIKE ? OR n1.id = ?) "
        f"AND e.relation IN {_GRAPH_RELS} ORDER BY e.relation LIMIT 40")
    dependents_sql = (
        "SELECT DISTINCT e.relation AS rel, n1.label AS name, n1.source_file AS file "
        "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
        "WHERE (n2.source_file LIKE ? OR n2.norm_label LIKE ? OR n2.id = ?) "
        f"AND e.relation IN {_GRAPH_RELS} ORDER BY e.relation LIMIT 40")
    like = f"%{target}%"
    p = [like, like.lower(), target]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            deps = await _gquery(client, deps_sql, p)
            dependents = await _gquery(client, dependents_sql, p)
    except Exception as e:
        return f"graph_neighbors: graph unavailable ({type(e).__name__}) -- proceed without it."
    if deps is None and dependents is None:
        return "graph_neighbors: code graph not seeded yet -- proceed without it."
    out = [f"GRAPH NEIGHBORHOOD for '{target}':",
           "DEPENDS ON (you reference these -- keep their signatures):"]
    out += [f"  {r['rel']} -> {r['name']} ({r['file']})" for r in (deps or [])] or ["  (none found)"]
    out.append("DEPENDED ON BY (these break if you change the contract):")
    out += [f"  {r['rel']} <- {r['name']} ({r['file']})" for r in (dependents or [])] or ["  (none found)"]
    return "\n".join(out)


@mcp.tool()
async def graph_path(src: str, dst: str) -> str:
    """Shortest connection path (<=5 hops) between two files/symbols, via a
    bounded, cycle-guarded recursive traversal of the code graph. The graph is
    undirected, so this walks edges in BOTH directions -- it shows how a change
    in one place can reach another (call/import/containment chain).

    Args:
        src: source file/symbol fragment.
        dst: destination file/symbol fragment.
    """
    # Undirected: at each step move to the OTHER endpoint of any incident edge.
    # Resolve endpoints among CODE nodes only (the ~360 'rationale' annotation
    # nodes also match a bare %fragment% and would mis-resolve the target).
    nxt = "CASE WHEN e.src=r.id THEN e.dst ELSE e.src END"
    sql = (
        "WITH RECURSIVE "
        "s AS (SELECT id FROM code_nodes WHERE file_type='code' "
        "AND (id=? OR source_file LIKE ? OR norm_label LIKE ?) "
        "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, length(source_file) LIMIT 1), "
        "t AS (SELECT id FROM code_nodes WHERE file_type='code' "
        "AND (id=? OR source_file LIKE ? OR norm_label LIKE ?) "
        "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, length(source_file) LIMIT 1), "
        "reach(id, depth, path) AS ("
        "  SELECT id, 0, [id] FROM s "
        "  UNION ALL "
        f"  SELECT {nxt}, r.depth+1, list_append(r.path, {nxt}) "
        "  FROM reach r JOIN code_edges e ON (e.src=r.id OR e.dst=r.id) "
        f"  WHERE r.depth < 5 AND e.relation <> 'rationale_for' AND NOT list_contains(r.path, {nxt})) "
        "SELECT depth, path FROM reach WHERE id = (SELECT id FROM t) ORDER BY depth LIMIT 1")
    sl, dl = f"%{src}%", f"%{dst}%"
    p = [src, sl, sl.lower(), src, dst, dl, dl.lower(), dst]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            rows = await _gquery(client, sql, p)
    except Exception as e:
        return f"graph_path: graph unavailable ({type(e).__name__})."
    if not rows:
        return (f"graph_path: no path from '{src}' to '{dst}' within 5 hops "
                "(or graph not seeded).")
    r = rows[0]
    return f"PATH {src} -> {dst} ({r.get('depth')} hops): " + " -> ".join(r.get("path", []))


if __name__ == "__main__":
    mcp.run(transport="stdio")