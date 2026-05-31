"""
build_routing.py -- pure, stdlib-only glue for the goose build path.

Shared by goose_runner.py (the architect orchestrator) and
mcp_servers/builder_mcp.py (the codegen delegate) so that a directive's
complexity routes BOTH halves up the ladder, and the LIVE goose build emits the
canonical `build_artifact` mesh row that the ingestor / governor / publisher
read. Kept dependency-free (no mcp, no httpx, no host paths) so it imports
cleanly in CI -- the daemons that use it do not.

Routing model (PR #16): a directive's complexity selects a `zo-ladder-{tier}`
model alias. goose_runner sets it as GOOSE_MODEL (architect) and ZO_BUILD_TIER
(codegen, read by builder_mcp); the ladder_shim maps the alias to a START rung.
Default low = zo-ladder-low = rung 0 (MiniMax) = exactly the prior behaviour.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

# Aliases MUST match keys in escalation.MODEL_TASK_MAP (PR #16); the drift guard
# in test_build_routing.py asserts this.
COMPLEXITY_TO_ALIAS = {
    "low": "zo-ladder-low",
    "medium": "zo-ladder-medium",
    "high": "zo-ladder-high",
    "critical": "zo-ladder-critical",
    "unknown": "zo-ladder-low",
}
DEFAULT_ALIAS = "zo-ladder-low"          # rung 0 (MiniMax) -- prior behaviour

BUILDER_AGENT_ID = "t1.zo_sentinel_builder"   # so the ingestor/publisher consume it
BUILD_ARTIFACT_TYPE = "build_artifact"


def tier_for_complexity(complexity: Optional[str]) -> str:
    return COMPLEXITY_TO_ALIAS.get((complexity or "").strip().lower(), DEFAULT_ALIAS)


def build_env_for(directive: dict) -> dict:
    """Per-directive env for the Goose subprocess: routes the architect
    (GOOSE_MODEL) + codegen (ZO_BUILD_TIER) by complexity and carries task/phase
    so builder_mcp can stamp a complete build_artifact row."""
    alias = tier_for_complexity(directive.get("complexity"))
    return {
        "GOOSE_MODEL": alias,
        "ZO_BUILD_TIER": alias,
        "ZO_BUILD_TASK": str(directive.get("key") or directive.get("directive_id")
                             or directive.get("id") or ""),
        "ZO_BUILD_PHASE": str(directive.get("phase", "")),
    }


def build_artifact_row(file: str, content_bytes: int, context_type: str,
                       tier: str, model: str = "", backend: str = "",
                       phase: str = "", task: str = "",
                       built_at: Optional[str] = None) -> dict:
    """The mesh_memory `build_artifact` row a live goose build emits. The base
    schema {file, built_at, phase, bytes, interface, task} matches what the
    ingestor reads; tier/model/backend are the new ladder provenance the
    publisher surfaces as a `ladder:<tier>` label."""
    return {
        "agent_id": BUILDER_AGENT_ID,
        "memory_type": BUILD_ARTIFACT_TYPE,
        "content": json.dumps({
            "file": file,
            "built_at": built_at or datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "bytes": content_bytes,
            "interface": context_type,
            "task": task,
            "tier": tier,
            "model": model,
            "backend": backend,
        }),
        "importance": 0.5,
    }
