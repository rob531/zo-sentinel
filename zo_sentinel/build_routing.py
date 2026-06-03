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
# medium routes to its own Gemini rung. NB: PR #58 briefly pinned this to rung-0
# on a "medium times out" theory -- but that symptom was actually the ladder_shim
# missing RcGeminiAPIKey, so EVERY gemini rung 502'd (not a slow rung, not a
# timeout). Fixed by relaunching the keyed shim (ladder_shim_with_keys.sh);
# medium returns clean code on the Gemini rung again, so the pin is reverted.
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


def resolve_directive_id(d: dict) -> str:
    """Stable id for a directive. Falls back to `task` -- the field the directive
    generator uses as its canonical identifier -- BEFORE giving up to "unknown".

    Without the `task` fallback every generator directive (which carries no
    id/key) collapses to directive_id="unknown", dedups to a single entry, and
    is skipped forever as already-built (directives/unknown.done.json exists).
    Order: explicit directive_id > id > key > task > "unknown"."""
    return (str(d.get("directive_id") or "") or str(d.get("id") or "")
            or d.get("key") or d.get("task") or "unknown")


def directive_content(d: dict) -> Optional[str]:
    """Resolve the build spec a directive carries, across the field names the
    different producers use, so goose can pass it to the architect as the task.

    The directive generator emits {task, handler, output_file, description, ...}
    -- the spec lives in `description` (+ output_file), NOT content/goal/spec.
    goose's pending loader required content/goal/spec and silently dropped every
    generator directive. Falls back description -> (Target file + description)."""
    for k in ("content", "goal", "spec"):
        v = d.get(k)
        if v:
            return v
    desc = d.get("description")
    if desc:
        out = d.get("output_file")
        return f"Target file: {out}\n\n{desc}" if out else desc
    return None


def build_env_for(directive: dict) -> dict:
    """Per-directive env for the Goose subprocess: routes the architect
    (GOOSE_MODEL) + codegen (ZO_BUILD_TIER) by complexity and carries task/phase
    so builder_mcp can stamp a complete build_artifact row."""
    codegen_tier = tier_for_complexity(directive.get("complexity"))
    return {
        # PHASE 1 (2026-06-03): PIN the architect/builder model to one reliable
        # coder (MiniMax rung-0) for the WHOLE goose session. escalation.ask() is
        # per-call with NO session affinity, so a complexity-routed GOOSE_MODEL
        # lets different rungs answer different turns of one multi-turn build ->
        # incoherent edits (MiniMax writes, Gemini "fixes", Gemma re-breaks). A
        # pinned rung-0 (MiniMax always returns non-empty -> never escalates
        # mid-build) gives goose's write/verify/iterate loop a coherent brain.
        # Codegen TIER is kept complexity-routed for the delegate_to_builder
        # fallback + build_artifact provenance. Phase 2 layers a multi-model
        # ensemble on top for high/critical (diversity at the CANDIDATE level).
        "GOOSE_MODEL": DEFAULT_ALIAS,          # zo-ladder-low = MiniMax rung 0 (pinned)
        "ZO_BUILD_TIER": codegen_tier,
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
