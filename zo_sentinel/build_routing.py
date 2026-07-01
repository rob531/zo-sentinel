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
import os
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
_CAPABLE_RECIPES = {"module_from_exemplar", "webapp_backend_fastapi", "webapp_frontend_react", "webapp_fullstack"}

# Phase 5 escalation ladder (cost-ordered). A FAILED directive re-asserts UP this
# list on its next attempt -- one alias per attempt (preserve #73; the bump is
# BETWEEN attempts, never mid-build, since each retry is a fresh goose subprocess
# pinned to one model). Indices map to escalation.py rungs: low=0, medium=1,
# high=11, critical=15. zo-ladder-critical (rung 15 = claude-sonnet-4-5) is PAID,
# so a non-critical directive is capped at the top FREE rung (zo-ladder-high); the
# escalation.py cost gate (LADDER_PAID_OK_TASKS) is the ultimate spend backstop.
_ESCALATION_LADDER = ["zo-ladder-low", "zo-ladder-medium", "zo-ladder-high",
                      "zo-ladder-critical"]
_FREE_CAP_INDEX = 2   # zo-ladder-high -- highest rung reachable without paid credit

BUILDER_AGENT_ID = "t1.zo_sentinel_builder"   # so the ingestor/publisher consume it
BUILD_ARTIFACT_TYPE = "build_artifact"


def _escalate_alias(base: str, attempt: int, complexity: str) -> str:
    """Bump `base` UP the escalation ladder by `attempt` steps. Non-critical
    directives are capped at the top free rung; only complexity=critical may reach
    the paid critical rung (and even then escalation.py's cost gate governs spend)."""
    try:
        start = _ESCALATION_LADDER.index(base)
    except ValueError:
        start = 0
    target = min(start + attempt, len(_ESCALATION_LADDER) - 1)
    if complexity != "critical":
        target = min(target, _FREE_CAP_INDEX)
    return _ESCALATION_LADDER[target]


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


def directive_type_of(directive: dict) -> str:
    """The class key the failure_matrix is grouped by (matches goose_runner's
    build_provenance derivation): interface > context_type > 'utility'."""
    return str(directive.get("interface") or directive.get("context_type") or "utility")


def best_model_from_matrix(rows, directive_type: str, complexity: str,
                           min_attempts: int = 20, exclude: Optional[str] = None,
                           min_success: float = 0.0):
    """Pick the highest-success model for this directive_type x complexity from
    failure_matrix `rows` (already fetched; each row: directive_type, complexity,
    model, attempts, success_pct). Skips empty-model rows (the 0%-success routing
    bug), rows below min_attempts (small-sample flukes), and `exclude` (the model
    that just ghosted, for escalation). Returns a model alias/name, or None so the
    caller falls back to the static complexity route. PURE -- no IO."""
    dt = (directive_type or "").strip().lower()
    cx = (complexity or "").strip().lower()
    best, best_pct = None, -1.0
    for r in rows or []:
        if str(r.get("directive_type", "")).strip().lower() != dt:
            continue
        if str(r.get("complexity", "")).strip().lower() != cx:
            continue
        model = str(r.get("model", "")).strip()
        if not model or model == exclude:
            continue
        try:
            attempts = int(r.get("attempts", 0))
            pct = float(r.get("success_pct", 0))
        except (TypeError, ValueError):
            continue
        if attempts >= min_attempts and pct >= min_success and pct > best_pct:
            best, best_pct = model, pct
    return best


def build_env_for(directive: dict, attempt: int = 0, matrix_rows=None) -> dict:
    """Per-directive env for the Goose subprocess: routes the architect
    (GOOSE_MODEL) + codegen (ZO_BUILD_TIER) by complexity and carries task/phase
    so builder_mcp can stamp a complete build_artifact row.

    `attempt` is the prior ghost-retry count (Phase 5). When ZO_ESCALATE is set and
    attempt>0, the FAILED directive re-asserts UP the ladder -- still ONE alias for
    this whole attempt (#73; the bump is between attempts, not mid-build). When
    ZO_ESCALATE is unset, attempt is IGNORED and the pinned env is returned exactly
    as before -- zero behaviour change until the flag is flipped."""
    codegen_tier = tier_for_complexity(directive.get("complexity"))
    complexity = (directive.get("complexity") or "").strip().lower()
    # The goose builder is PINNED to ONE model for the whole session (escalation
    # has no session affinity, so a routed model would switch rungs mid-build ->
    # incoherent edits, #73). low/high/critical stay on rung-0 MiniMax M2.7;
    # MEDIUM pins to zo-ladder-medium, which now STARTS at the MiniMax-M3 rung -- a
    # stronger MiniMax primary that returns non-empty and BACKS OFF on 429 rather
    # than escalating, so it is still ONE coherent model per build, just a better
    # one for the harder directives. ZO_BUILD_TIER stays complexity-routed for the
    # delegate_to_builder fallback + build_artifact provenance.
    # Static route (prior behaviour): medium -> MiniMax-M3 rung, else rung-0 MiniMax.
    static_model = "zo-ladder-medium" if complexity == "medium" else DEFAULT_ALIAS
    # CAPABLE-RUNG ROUTING (2026-06-28): the validated module_from_exemplar lane needs a
    # tool-capable, schema-competent rung. MiniMax (the default builder pin) reliably
    # hallucinates the real columns even with the schema injected, while the genuinely
    # capable rungs were never reached -- the Zo-routed strong models 402 (Payment Required)
    # and the goose primary never fails over off MiniMax (escalation stops at the first
    # non-empty response; it has no quality gate). Pin exemplar-lane builds to the LIVE,
    # tool-capable coder window (NVIDIA NIM -> Cerebras -> Mistral Codestral -> Groq, rungs
    # 17-20, all FREE) so a capable model actually writes the module, grounded by the
    # recipe-injected real schema + exemplar.
    if str(directive.get("recipe", "")).strip() in _CAPABLE_RECIPES:
        static_model = "zo-ladder-nvidia"
    # Matrix-driven (evidence > static): route to the model that ACTUALLY builds this
    # directive_type x complexity best, per failure_matrix. Falls back to static when
    # the matrix is thin/absent. (rows are fetched + cached by the daemon -- this
    # module stays pure.)
    rows = matrix_rows or []
    dtype = directive_type_of(directive)
    base = best_model_from_matrix(rows, dtype, complexity) or static_model
    goose_model = base
    if attempt > 0 and os.environ.get("ZO_ESCALATE"):
        # Matrix-aware escalation. A ghost retries on the best PROVEN-GOOD alternative
        # model (>= floor success, NOT the one that just failed) -- never a blind climb
        # into zo-ladder-high (~14% in the matrix). If the matrix has data but no good
        # alternative, RETRY THE WINNER (a fresh attempt on the proven model beats a
        # worse rung). Only fall back to the static ladder climb when the matrix has NO
        # data for this class at all.
        floor = float(os.environ.get("ZO_ESCALATE_FLOOR", "40"))
        nxt = best_model_from_matrix(rows, dtype, complexity, exclude=base, min_success=floor)
        if nxt:
            goose_model = nxt
        elif not best_model_from_matrix(rows, dtype, complexity):   # no data for this class
            goose_model = _escalate_alias(
                base if base in _ESCALATION_LADDER else static_model, attempt, complexity)
        # else: matrix has data but no better option -> keep base (retry the winner)
    # The validated module_from_exemplar lane REQUIRES a capable, tool-capable rung: MiniMax
    # (the matrix/static default) either over-seeds NOT NULL columns or writes inline-mock
    # models, so it never clears the self-test gate. Make the capable coder window the FINAL
    # word over the matrix + escalation picks for this lane (free rungs 17-20).
    if str(directive.get("recipe", "")).strip() in _CAPABLE_RECIPES:
        # Rotate the capable tool-capable coder window by attempt so a quality failure
        # FAILS OVER to a different capable model instead of re-hitting the same rung
        # (escalation.ask has no quality gate -- it short-circuits on the first non-empty
        # response -- so cross-model failover on a bad build must happen here).
        _CAPABLE = ["zo-ladder-nvidia", "zo-ladder-mistral", "zo-ladder-cerebras", "zo-ladder-groq"]
        goose_model = _CAPABLE[int(attempt) % len(_CAPABLE)]
    goose_model = goose_model or DEFAULT_ALIAS          # never route to an empty model
    return {
        "GOOSE_MODEL": goose_model,
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
    publisher surfaces as a `ladder:<tier>` label.

    `id` is supplied EXPLICITLY (epoch microseconds, BIGINT). write_service._write
    does `INSERT OR IGNORE INTO mesh_memory` for these rows, which SILENTLY drops a
    row whose service-assigned id collides or is NULL -- /write still returns
    ok:True, no error. After the 2026-06-14 reboot the auto-id path started
    colliding, so EVERY goose build_artifact was dropped (0 rows since 22:06Z) ->
    the publisher went blind -> no PRs, even though builds kept succeeding and
    landing in build_provenance. A caller-supplied unique id (well above the ~20k
    sequential range, correct BIGINT type) makes the insert collision-proof
    regardless of write_service's _next_id runtime state or _NO_AUTO_ID set."""
    ts = built_at or datetime.now(timezone.utc).isoformat()
    return {
        "id": int(datetime.now(timezone.utc).timestamp() * 1_000_000),
        "agent_id": BUILDER_AGENT_ID,
        "memory_type": BUILD_ARTIFACT_TYPE,
        "content": json.dumps({
            "file": file,
            "built_at": ts,
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


def build_provenance_row(directive_id: str, directive_type: str, complexity: str,
                         model: str, success: bool, smoke_result: str,
                         attempt: int = 1, rescue_count: int = 0,
                         output_path: str = "", output_bytes: int = 0,
                         error: str = "", engine: str = "goose",
                         backend: str = "goose_developer",
                         built_at: Optional[str] = None) -> dict:
    """One `build_provenance` row per build ATTEMPT -- the matrix substrate
    (Phase 4). Captures the per-attempt rung+outcome the goose path never recorded
    (build_provenance was defined-but-unwired). `build_id` is deterministic
    (directive:outcome:attempt:day) so a re-emit of the same attempt is idempotent
    under write_service's INSERT OR IGNORE (build_id PK), while a genuine rebuild on
    a later day records a fresh row."""
    ts = built_at or datetime.now(timezone.utc).isoformat()
    build_id = f"{directive_id}:{smoke_result}:a{attempt}:{ts[:10]}"
    return {
        "build_id": build_id,
        "directive_id": directive_id,
        "directive_type": directive_type,
        "complexity": complexity,
        "engine": engine,
        "model": model,
        "backend": backend,
        "smoke_result": smoke_result,
        "rescue_count": rescue_count,
        "success": success,
        "output_path": output_path,
        "output_bytes": output_bytes,
        "error": error,
        "built_at": ts,
    }
