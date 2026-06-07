#!/usr/bin/env python3
"""
index_graph.py -- contextual graph indexing lifecycle (blueprint #2).

Builds the codebase's class maps / call graph into `graphify-out/graph.json`
(tree-sitter, 28+ langs) so the agent control planes get deep path discovery.

graphifyy is a `uv tool`, so it never pollutes the container venv:
    uv tool install "graphifyy[mcp]"     # one-time, isolated

Two indexing modes:

  code-only  (default, `update`)  -- AST re-extraction of source only. No LLM,
                                     no network, deterministic. Always safe.

  semantic   (`--semantic`)       -- also semantically extracts docs/papers.
                                     This needs an LLM backend. Rather than call
                                     an EXTERNAL API (which would violate the
                                     repo's "No external HTTP" constraint,
                                     CLAUDE.md:251), we point graphify's
                                     OpenAI-compatible backend at the LOCAL
                                     ladder shim on 127.0.0.1:8796/v1 -- the same
                                     loopback front door goose_runner.py uses
                                     (its keys are AgentVault-hydrated via
                                     escalation.py). So doc extraction reuses the
                                     one sanctioned outbound path (the ladder),
                                     and this process itself makes no external
                                     call. If the shim isn't reachable we fall
                                     back to code-only rather than reaching out.

The MCP server that serves the graph to agents is registered in .mcp.json:
    uv run --with "graphifyy[mcp]" python -m graphify.serve graphify-out/graph.json

Usage:
    uv run tools/index_graph.py                 # code-only (no LLM)
    uv run tools/index_graph.py --semantic      # docs too, via local ladder
    uv run tools/index_graph.py --root . --semantic
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The local, OpenAI-compatible ladder shim. Keys are AgentVault-hydrated inside
# escalation.py; the shim holds none itself. Loopback only -> not external HTTP.
LADDER_URL = os.environ.get("ZO_LADDER_URL", "http://127.0.0.1:8796/v1")
LADDER_HEALTH = LADDER_URL.rsplit("/v1", 1)[0] + "/health"


def _has(tool: str) -> bool:
    return shutil.which(tool) is not None


def _graphify_prefix() -> list[str]:
    """Prefer an installed `graphify`; else run it ephemerally through uv."""
    if _has("graphify"):
        return ["graphify"]
    if _has("uv"):
        return ["uv", "run", "--with", "graphifyy[mcp]", "graphify"]
    raise FileNotFoundError('neither `graphify` nor `uv` on PATH -- run: '
                            'uv tool install "graphifyy[mcp]"')


def _ladder_up() -> bool:
    """Is the local ladder shim reachable? Best-effort; never raises."""
    try:
        import urllib.request
        with urllib.request.urlopen(LADDER_HEALTH, timeout=3) as r:  # loopback only
            return r.status == 200
    except Exception:
        return False


def _run(cmd: list[str], root: Path, env: dict | None = None) -> int:
    print(f"[index_graph] {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, cwd=str(root), text=True, timeout=1800,
                              env=env).returncode
    except FileNotFoundError as e:
        print(f"[index_graph] {e}", file=sys.stderr)
        return 3
    except subprocess.TimeoutExpired:
        print("[index_graph] indexing timed out after 1800s", file=sys.stderr)
        return 4


def index(root: Path, semantic: bool) -> int:
    pre = _graphify_prefix()

    if semantic:
        if _ladder_up():
            # Route graphify's OpenAI-compatible backend at the local ladder.
            # Mirrors goose_runner.py: dummy key (the shim ignores it), base_url
            # -> loopback shim, which does MiniMax->Gemini->... with vault keys.
            env = {**os.environ,
                   "OPENAI_BASE_URL": LADDER_URL,
                   "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or "dummy_key_for_shim"}
            print(f"[index_graph] semantic mode via LOCAL ladder shim ({LADDER_URL}) "
                  "-- docs extracted with no external HTTP")
            rc = _run([*pre, str(root), "--backend", "openai"], root, env)
            if rc == 0:
                return _verify(root)
            print("[index_graph] semantic build failed -- falling back to code-only",
                  file=sys.stderr)
        else:
            print(f"[index_graph] ladder shim not reachable at {LADDER_HEALTH} "
                  "-- using code-only (refusing to reach an external LLM)",
                  file=sys.stderr)

    # code-only: AST re-extraction, no LLM, no network.
    rc = _run([*pre, "update", str(root)], root)
    return _verify(root) if rc == 0 else rc


def _verify(root: Path) -> int:
    out = root / "graphify-out" / "graph.json"
    if out.is_file():
        print(f"[index_graph] graph written: {out} ({out.stat().st_size} bytes)")
        return 0
    print(f"[index_graph] build reported success but {out} missing", file=sys.stderr)
    return 5


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the graphifyy knowledge graph")
    ap.add_argument("--root", default=".", help="directory to index (default: cwd)")
    ap.add_argument("--semantic", action="store_true",
                    help="also extract docs via the LOCAL ladder shim (loopback)")
    args = ap.parse_args(argv)
    return index(Path(args.root).resolve(), args.semantic)


if __name__ == "__main__":
    raise SystemExit(main())
