#!/usr/bin/env python3
"""
directive_mcp.py -- stdio MCP server for the Directive Architect recipe.

Sibling of zo_sentinel/mcp_servers/builder_mcp.py. Where builder_mcp exposes
delegate_to_builder so Goose-Architect can WRITE FILES, directive_mcp exposes
propose_directive so Goose-Directive-Architect can WRITE DIRECTIVES.

IDEMPOTENCY GUARANTEES:
  - Writes ONLY to /home/workspace/zo_sentinel/directives/proposed/
  - Never writes to pending/ (where goose_runner picks up live work)
  - Never modifies any existing file
  - Filename hashing matches the existing sentinel_directive_generator
    convention: gen_<md5_first_8>_<task[:35]>.json
  - If a proposed file with the same name already exists, returns
    {"status": "duplicate", ...} without overwriting
  - Validator equivalent runs server-side; rejections are returned, never
    silently dropped

READ-ONLY DEPENDENCIES:
  - Imports gate_quality_state (read-only API) if available
  - Reads sentinel_directive_generator.ALREADY_BUILT / PROTECTED_FILES sets
    if the module is importable; otherwise hardcoded fallback lists below
  - HTTP GETs to write_service /query at 127.0.0.1:8772 for mesh_memory
    failure history (read-only endpoint)
  - Never POSTs to write_service /write (only ws_query equivalent)

This module does NOT replace sentinel_directive_generator. It is invoked only
when sentinel_directive_generator_goose.py runs a goose subprocess with
directive_architect.yaml. If that daemon is not registered with supervisord,
this MCP server is never started.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# MCP stdio framing -- minimal hand-rolled to avoid extra dependency surface.
# Matches the protocol used by builder_mcp.py (Goose stdio extension).
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except Exception as e:
    sys.stderr.write(
        f"directive_mcp: failed to import mcp.server.fastmcp ({e}). "
        f"Install the same MCP SDK that builder_mcp.py uses.\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SENTINEL_DIR = Path("/home/workspace/zo_sentinel")
DIRECTIVE_DIR = SENTINEL_DIR / "directives"
PROPOSED_DIR = DIRECTIVE_DIR / "proposed"   # NEW: this MCP only writes here
PENDING_DIR = DIRECTIVE_DIR / "pending"     # read-only from this module
LOG_PATH = Path("/home/workspace/logs/directive_mcp.log")

PROPOSED_DIR.mkdir(parents=True, exist_ok=True)


def _already_done(directive_id: str, task: str) -> bool:
    """Authoritative already-built check. The promoter skips any directive whose
    <id>.done.json sentinel exists (or whose completed file sits in done/). Reject
    re-proposals of those HERE so completed work never re-enters proposed/ and never
    clogs it to the depth cap -- the mechanism behind the architect's +0 stall. This
    reconciles the architect's code-GRAPH view of 'built' (which disagrees when a build
    ghosted) with the pipeline's authoritative DONE record. Best-effort."""
    try:
        for name in {directive_id, task}:
            if name and ((DIRECTIVE_DIR / f"{name}.done.json").exists()
                         or (DIRECTIVE_DIR / "done" / f"{name}.json").exists()):
                return True
    except Exception:
        pass
    return False
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        sys.stderr.write(line)


# ---------------------------------------------------------------------------
# Validator -- mirrors sentinel_directive_generator.validate_directive
# ---------------------------------------------------------------------------

VALID_HANDLERS = {"generate_file", "write_raw", "run_script"}
VALID_COMPLEXITY = {"low", "medium", "high"}
VALID_BREAKER_ACTIONS = {"investigate", "reset", "accept"}
REQUIRED_FIELDS = {"task", "handler", "description"}   # output_file required only for non-edit tasks (see _validate)

# Edit-class task verbs MODIFY existing files -> they declare NO creation output.
# Mirrors zo_sentinel/build_completion.py EDIT_TASK_PREFIXES (keep in sync; that
# copy is the authority used by goose_runner's ghost-guard).
EDIT_TASK_PREFIXES = ("wire_", "rewire_", "unwire_", "integrate_",
                      "migrate_", "refactor_", "patch_")


def _is_edit_task(task: str) -> bool:
    return str(task or "").startswith(EDIT_TASK_PREFIXES)

# Fallback hardcoded sets, used ONLY if sentinel_directive_generator cannot
# be imported. Keeping them here ensures the MCP fails closed (rejecting too
# much) rather than open (proposing forbidden rebuilds).
_FALLBACK_PROTECTED = {
    "advanced_filter_api.py", "approval_workflow.py", "attestation_engine.py",
    "bulk_assess_api.py", "comparison_api.py", "dashboard.html",
    "dashboard_api.py", "forensic_detail_api.py", "full_schema_bootstrap.py",
    "inference_router_service.py", "manual_override_api.py",
    "mcp_scanner.py", "registry_api.py", "rug_pull_monitor.py",
    "search_api.py", "sentinel_status.html", "signal_analyser.py",
    "threat_intel_ingestor.py", "trust_synthesiser.py", "ui_server.py",
    "write_service.py", "manifest_blast_radius.py",
}


def _import_validator_sets():
    """Try to import live ALREADY_BUILT / PROTECTED_FILES from the existing
    generator. Read-only — we never mutate the source module."""
    try:
        sys.path.insert(0, str(SENTINEL_DIR))
        import sentinel_directive_generator as sdg  # type: ignore
        already = set(getattr(sdg, "ALREADY_BUILT", set()))
        protected = set(getattr(sdg, "PROTECTED_FILES", _FALLBACK_PROTECTED))
        return already, protected
    except Exception as e:
        _log(f"validator-set import failed ({e}); using fallbacks")
        return set(), _FALLBACK_PROTECTED


def _gate_state():
    """Read gate_quality_state if importable. Read-only API."""
    try:
        sys.path.insert(0, str(SENTINEL_DIR))
        import gate_quality_state as gqs  # type: ignore
        return gqs
    except Exception as e:
        _log(f"gate_quality_state import failed ({e}); returning None")
        return None


def _validate(d: dict) -> tuple[bool, str]:
    if not isinstance(d, dict):
        return False, "not a dict"
    missing = REQUIRED_FIELDS - d.keys()
    if missing:
        return False, f"missing fields: {sorted(missing)}"
    if d["handler"] not in VALID_HANDLERS:
        return False, f"invalid handler: {d['handler']}"
    if d.get("complexity") and d["complexity"] not in VALID_COMPLEXITY:
        return False, f"invalid complexity: {d['complexity']}"
    output = (d.get("output_file") or "").strip()
    # Edit-class tasks modify existing files and declare no creation output;
    # every other task must name the file it will create.
    if not _is_edit_task(d.get("task", "")) and not output:
        return False, "non-edit task must declare output_file"
    if output:
        from zo_sentinel.build_completion import output_file_is_sane
        _ok_of, _reason_of = output_file_is_sane(output)
        if not _ok_of:
            return False, _reason_of
        already, protected = _import_validator_sets()
        if output in already:
            return False, f"already built: {output}"
        if output in protected:
            return False, f"protected (hand-calibrated, do not regenerate): {output}"
        gqs = _gate_state()
        if gqs is not None:
            try:
                ok, reason = gqs.may_rebuild(output)
                if not ok:
                    return False, (
                        f"quality gate blocks rebuild of {output}: {reason}. "
                        f"Use propose_breaker_action instead."
                    )
            except Exception as e:
                _log(f"may_rebuild check raised {e}; failing closed")
                return False, f"breaker check error: {e}"
    if len(d.get("description", "")) < 200:
        return False, ("description too thin (<200 chars) -- goose builds clear specs and "
                       "GHOSTS vague ones. Provide interface + inputs + output + constraints "
                       "+ an acceptance self-test (see the directive_architect SPEC QUALITY block).")
    return True, "ok"


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("zo-directive-bridge")


@mcp.tool()
def read_gate_quality_state(file: str | None = None) -> dict:
    """Return quarantine state for one file, or all quarantined files."""
    gqs = _gate_state()
    if gqs is None:
        return {"status": "unavailable", "files": {}}
    try:
        # gate_quality_state's API surface isn't 100% documented here; we try
        # a few common shapes and degrade gracefully.
        if file is not None and hasattr(gqs, "state_for"):
            return {"status": "ok", "file": file, "state": gqs.state_for(file)}
        if hasattr(gqs, "all_state"):
            data = gqs.all_state()
            if file is not None:
                data = {file: data.get(file, {"status": "unknown"})}
            return {"status": "ok", "files": data}
        # Fallback: probe may_rebuild for known files.
        already, protected = _import_validator_sets()
        out = {}
        for f in (already | protected):
            try:
                ok, reason = gqs.may_rebuild(f)
                if not ok:
                    out[f] = {"may_rebuild": False, "reason": reason}
            except Exception:
                pass
        return {"status": "ok", "files": out}
    except Exception as e:
        _log(f"read_gate_quality_state error: {e}")
        return {"status": "error", "error": str(e), "files": {}}


@mcp.tool()
def read_already_built() -> dict:
    """Return the hardcoded ALREADY_BUILT set from the live generator."""
    already, _ = _import_validator_sets()
    return {"status": "ok", "count": len(already), "files": sorted(already)}


@mcp.tool()
def read_protected_files() -> dict:
    """Return the hardcoded PROTECTED_FILES set from the live generator."""
    _, protected = _import_validator_sets()
    return {"status": "ok", "count": len(protected), "files": sorted(protected)}


@mcp.tool()
def read_pending_directives() -> dict:
    """List task names from BOTH pending/ and proposed/ to enable dedupe."""
    out = {"pending": [], "proposed": []}
    for sub, key in ((PENDING_DIR, "pending"), (PROPOSED_DIR, "proposed")):
        if not sub.exists():
            continue
        for p in sub.glob("*.json"):
            name = p.name
            if name.endswith(".done.json") or name.endswith(".failed.json"):
                continue
            try:
                d = json.loads(p.read_text())
                out[key].append(d.get("task", name))
            except Exception:
                out[key].append(name)
    out["status"] = "ok"
    return out


@mcp.tool()
def read_failure_history(hours: int = 24) -> dict:
    """Read recent failure signals from mesh_memory.

    Sources:
      - escalation_call / build_failure / directive_generation
          (legacy tower-side signals — unchanged behavior)
      - gh_check_failure
          (NEW: GitHub Actions evaluator failures fed back by
           zo_sentinel/evaluators/gh_actions_fetcher.py — the cheap
           Goose-T2 reverse-feed loop)

    Read-only HTTP GET to write_service /query (the published read
    endpoint). Returns at most 50 rows.
    """
    try:
        import requests
        sql = (
            "SELECT content, created_at, memory_type FROM mesh_memory "
            "WHERE memory_type IN ('escalation_call', 'build_failure', "
            "'directive_generation', 'gh_check_failure') "
            f"AND created_at > NOW() - INTERVAL {int(hours)} HOUR "
            "ORDER BY created_at DESC LIMIT 50"
        )
        r = requests.get(
            "http://127.0.0.1:8772/query",
            params={"sql": sql}, timeout=5,
        )
        if r.status_code != 200:
            return {"status": "error", "code": r.status_code, "rows": []}
        return {"status": "ok", "rows": r.json()}
    except Exception as e:
        _log(f"read_failure_history error: {e}")
        return {"status": "error", "error": str(e), "rows": []}


@mcp.tool()
def propose_directive(
    task: str,
    handler: str,
    description: str,
    output_file: str = "",
    complexity: str = "medium",
    phase: str | None = None,
    priority: float | None = None,
    rationale: str | None = None,
) -> dict:
    """Validate and write a directive JSON to directives/proposed/.

    output_file: the NEW file the task creates -- REQUIRED for creation tasks
    (build_*, etc.). LEAVE EMPTY for edit-class tasks (wire_/rewire_/integrate_/
    ...) that modify EXISTING files: they create no new file and are verified by
    process success + their own smoke-test. Stamping a bogus output_file=<task>.py
    on an edit task makes the ghost-guard fail it forever.

    Returns: {"status": "written"|"duplicate"|"rejected", "path": str?, "reason": str?}
    """
    # Edit-class tasks never create a <task>.py -- drop any output_file the model
    # supplied so the directive is honest and the ghost-guard trusts process
    # success (build_completion.is_edit_task does the same defensively).
    if _is_edit_task(task):
        output_file = ""
    d = {
        "task": task,
        "handler": handler,
        "output_file": output_file or None,
        "complexity": complexity,
        "description": description,
        "source": "directive_architect",
        "proposed_at": datetime.now(timezone.utc).isoformat(),
    }
    if phase is not None:
        d["phase"] = phase
    if priority is not None:
        d["priority"] = priority
    if rationale is not None:
        d["rationale"] = rationale

    ok, reason = _validate(d)
    if not ok:
        _log(f"REJECT {task}: {reason}")
        return {"status": "rejected", "reason": reason}

    if _already_done(d.get("directive_id") or task, task):
        _log(f"ALREADY-DONE {task}: done sentinel exists; not re-proposing")
        return {"status": "duplicate", "reason": "already built (done sentinel)", "task": task}

    key = hashlib.md5(task.encode()).hexdigest()[:8]
    fname = f"gen_{key}_{task[:35]}.json"
    path = PROPOSED_DIR / fname

    if path.exists():
        _log(f"DUPLICATE {task}: {path} already exists; not overwriting")
        return {"status": "duplicate", "path": str(path), "task": task}

    try:
        path.write_text(json.dumps(d, indent=2))
        _log(f"WRITTEN {task} -> {path}")
        return {"status": "written", "path": str(path), "task": task}
    except Exception as e:
        _log(f"WRITE FAILED {task}: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
def propose_breaker_action(file: str, action: str, rationale: str) -> dict:
    """Propose a breaker-targeting directive instead of a file rebuild.

    action: investigate | reset | accept

    Writes a directive with handler='run_script' and a special task prefix
    'breaker_action_' so downstream tooling can route it to a non-rebuild path.
    """
    if action not in VALID_BREAKER_ACTIONS:
        return {
            "status": "rejected",
            "reason": f"invalid action {action!r}; must be one of {sorted(VALID_BREAKER_ACTIONS)}",
        }
    if len(rationale or "") < 30:
        return {
            "status": "rejected",
            "reason": "rationale too short (<30 chars); explain WHY this action",
        }

    task = f"breaker_action_{action}_{Path(file).stem}"
    desc = (
        f"Quality-gate breaker action '{action}' for {file}. Rationale: {rationale}. "
        f"This directive does NOT rebuild {file}; it triggers a breaker workflow. "
        f"Proposed by directive_architect at {datetime.now(timezone.utc).isoformat()}."
    )
    d = {
        "task": task,
        "handler": "run_script",
        "output_file": f"breaker_actions/{task}.py",
        "complexity": "low",
        "description": desc,
        "source": "directive_architect",
        "breaker_action": {"file": file, "action": action, "rationale": rationale},
        "proposed_at": datetime.now(timezone.utc).isoformat(),
    }

    key = hashlib.md5(task.encode()).hexdigest()[:8]
    fname = f"gen_{key}_{task[:35]}.json"
    path = PROPOSED_DIR / fname
    if path.exists():
        return {"status": "duplicate", "path": str(path)}
    try:
        path.write_text(json.dumps(d, indent=2))
        _log(f"BREAKER {action} {file} -> {path}")
        return {"status": "written", "path": str(path), "action": action, "file": file}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    _log("directive_mcp starting (stdio)")
    mcp.run()
