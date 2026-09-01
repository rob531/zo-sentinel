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
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# DEFEND against a stale `argparse` BACKPORT shadowing the stdlib. A copy of the old
# PyPI `argparse` package lives in this host's site-packages and lacks
# BooleanOptionalAction, so when it wins import resolution `import mcp` dies with
# "cannot import name 'BooleanOptionalAction' from 'argparse'" -> the bridge never
# starts -> propose_directive is uncallable -> the architect is +0. Pin the stdlib
# dir to the FRONT of sys.path so `import argparse` always resolves to stdlib,
# regardless of PYTHONPATH / site ordering.
import sysconfig as _sysconfig
_stdlib_dir = _sysconfig.get_paths().get("stdlib")
if _stdlib_dir:
    sys.path.insert(0, _stdlib_dir)
    sys.modules.pop("argparse", None)
# The bridge must be import-SELF-SUFFICIENT: it is launched as a SCRIPT
# (`python3 zo_sentinel/mcp_servers/directive_mcp.py`), so sys.path[0] is its
# OWN directory -- `zo_sentinel.*` resolves only if the LAUNCHER happened to
# export PYTHONPATH. After a daemon-wrapper relaunch the architect env has no
# PYTHONPATH, so every CREATION proposal died inside _validate at
# `from zo_sentinel.build_completion import output_file_is_sane` with
# "No module named 'zo_sentinel'" -> goose swallowed it -> +0 (edit-class
# proposals skipped the import, which is why wire_* still landed). Same class
# as the argparse pin above: never depend on the launcher's environment.
import pathlib as _pathlib
_repo_root = str(_pathlib.Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(1, _repo_root)

# MCP stdio framing -- minimal hand-rolled to avoid extra dependency surface.
# Matches the protocol used by builder_mcp.py (Goose stdio extension).
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except Exception as e:
    sys.stderr.write(
        f"directive_mcp: failed to import mcp.server.fastmcp ({e}). "
        f"Install the same MCP SDK that builder_mcp.py uses.\n"
    )
    # Exit code 2 is the SCRIPT contract -- the architect launches this file as
    # `python3 zo_sentinel/mcp_servers/directive_mcp.py` -- and is UNCHANGED.
    # But sys.exit() raises SystemExit, which derives from BaseException, so an
    # IMPORTER guarding with `except Exception` cannot catch it. Under pytest it
    # aborts COLLECTION for the entire session (FU-158): 178 of 425 tests never
    # ran and the summary line still read clean. Re-raise the real ImportError
    # for importers; only the script path terminates.
    if __name__ == "__main__":
        sys.exit(2)
    raise


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Root is OVERRIDABLE but the DEFAULT IS UNCHANGED -- the tower behaves exactly
# as before. The override exists so the bridge can be exercised somewhere other
# than /home/workspace (goose-canary CI, a scratch checkout) without editing code.
#
# THIRD INSTANCE OF THE SAME SCAR (see the argparse pin and the PYTHONPATH fix
# above): anything that makes this module fail at IMPORT time takes the bridge
# down, and goose does NOT treat that as fatal -- it warns
# ("Failed to start extension ... continuing without it") and runs the session
# with only python/bash. The architect then presents as healthy and emits +0.
# So: never do unguarded filesystem work at import time against a path this
# module does not control.
SENTINEL_DIR = Path(os.environ.get("ZO_SENTINEL_DIR", "/home/workspace/zo_sentinel"))
DIRECTIVE_DIR = SENTINEL_DIR / "directives"
PROPOSED_DIR = DIRECTIVE_DIR / "proposed"   # NEW: this MCP only writes here
PENDING_DIR = DIRECTIVE_DIR / "pending"     # read-only from this module
LOG_PATH = Path(
    os.environ.get("ZO_DIRECTIVE_MCP_LOG", "/home/workspace/logs/directive_mcp.log")
)

try:
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
except OSError as _e:
    # Fail LOUD on the one surface goose actually surfaces (stderr), naming the
    # consequence rather than just the errno -- a silent +0 architect is the
    # expensive failure, not the mkdir.
    sys.stderr.write(
        f"directive_mcp: cannot create {PROPOSED_DIR} ({_e}). The bridge cannot "
        f"accept proposals, so the architect will emit +0 while appearing healthy. "
        f"Set ZO_SENTINEL_DIR to a writable root.\n"
    )
    # Exit code 2 is the SCRIPT contract (the architect launches this file as
    # `python3 zo_sentinel/mcp_servers/directive_mcp.py`) and is unchanged.
    # But sys.exit() raises SystemExit, which derives from BaseException, so an
    # IMPORTER guarding with `except Exception` cannot catch it and pytest
    # aborts COLLECTION for the whole session -- that is FU-158, fixed on main
    # for the mcp-import guard 12 lines above and re-introduced here if this
    # branch exits unconditionally. Same discipline, same reason: only the
    # script path terminates; importers get an ordinary OSError.
    if __name__ == "__main__":
        sys.exit(2)
    raise


_VERSION_RE = re.compile(r"(_v\d+)+$")           # trailing _v2 / _v3_v4 etc.
_DIAG_PREFIXES = ("investigate_", "diagnose_")   # diagnostic builds, not net-new work
_DIAG_CAP = int(os.environ.get("ZO_INVESTIGATE_CAP", "2"))
_diag_count = 0   # per-PROCESS; the bridge is stdio-respawned per architect cycle,
                  # so this is effectively per-cycle and resets automatically.


def _base_task(task: str) -> str:
    """Strip trailing version suffix: investigate_X_v4 -> investigate_X. The architect
    bumps the suffix to dodge the done-dedup and re-investigate the same thing forever
    (investigate_X_v2..v11). Collapsing to the base closes that escape hatch."""
    return _VERSION_RE.sub("", task or "")


def _base_already_done(base: str) -> bool:
    """True if the BASE task (any version) already has a done sentinel -- so a
    version-bumped re-proposal of completed work is caught."""
    if not base:
        return False
    try:
        for d in (DIRECTIVE_DIR, DIRECTIVE_DIR / "done"):
            if any(d.glob(f"{base}.done.json")) or any(d.glob(f"{base}_v*.done.json")):
                return True
            if any(d.glob(f"{base}.json")) or any(d.glob(f"{base}_v*.json")):
                return True
    except Exception:
        pass
    return False


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
        base = _base_task(task)               # version-collapse: investigate_X_v5 == done investigate_X
        if base and base != task and _base_already_done(base):
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

VALID_HANDLERS = {"generate_file", "write_raw", "run_script", "build_service"}
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
    # PARKED branch enforcement -- the snow/aidr external-client authorization
    # branch + approval_evidence_bundler are DEFERRED future work, not current. The
    # directive_architect recipe says so in prose, but the weak architect model
    # ignores it and lands parked work once the graph-aware already-built reject
    # steers it off built modules (observed 2026-06-23: it wrote
    # build_snow_connector_approval_workflow +1). Enforce deterministically here,
    # mirroring the already-built reject, with a steer back to current domains.
    _parked_probe = f"{d.get('task', '')} {d.get('output_file', '')}".lower()
    if any(_p in _parked_probe for _p in ("snow", "aidr", "approval_evidence_bundler")):
        return False, (
            "PARKED branch (snow/aidr external-client authorization, "
            "approval_evidence_bundler) -- a DEFERRED future branch, NOT current work. "
            "Do NOT propose build/wire/verify work for it. Propose a NEW capability in a "
            "CURRENT underbuilt domain instead: call list_domains and pick a thin/uncovered "
            "one, or target a concrete integration gap.")
    # SERVICE UNIT (SOA atomic unit, 2026-07-24): a build_service directive names a
    # SERVICE, not a file. The promoter fans it out into single-file directives;
    # the staged->active gate makes it live only when its contract proves a 200.
    if d["handler"] == "build_service":
        _task = str(d.get("task", ""))
        if not _task.startswith("build_service_"):
            return False, "build_service task must be named build_service_<snake_name>"
        _svc = _task[len("build_service_"):]
        if not _svc or not _svc.replace("_", "").isalnum():
            return False, "build_service_<snake_name>: snake_name missing/invalid"
        if len(str(d.get("description", "")) or "") < 200:
            return False, ("build_service description IS the service spec -- >=200 chars "
                           "naming route+prefix, tables/columns read, response shape, "
                           "and the acceptance assertions its contract must make")
        for _sd in ("active", "staged"):
            if (Path(__file__).resolve().parents[2] / "services" / _sd / _svc).exists():
                return False, f"service '{_svc}' already exists in services/{_sd}/ -- not net-new"
        return True, "ok"
    # SERVICE-UNIT REDIRECT (rejection-time teaching, the mechanism that provably
    # lands here -- same pattern as the PARKED and already-built rejects: prose in
    # the recipe gets ignored; a reject with a steer corrects the model in-context
    # at the moment of failure). A single-FILE directive that clearly declares an
    # HTTP surface is the old 2.9%-yield unit; redirect it to build_service.
    # Kill: ZO_SERVICE_UNIT_REDIRECT=0. Consumers/reports without routes pass.
    if (d["handler"] in ("generate_file", "write_raw")
            and os.environ.get("ZO_SERVICE_UNIT_REDIRECT", "1") != "0"):
        _surface_probe = f"{d.get('output_file', '')} {d.get('description', '')}"
        _looks_route = bool(re.search(
            r"APIRouter|@router\.|FastAPI router|\b(GET|POST|PUT|DELETE|PATCH) /"
            r"|_api\.py\b|_router\.py\b|\bendpoint\b", _surface_probe))
        if _looks_route:
            try:  # report card: one JSONL row per lesson, so the chairman review can
                  # grade convergence (redirects/day -> 0 while .expanded/day rises)
                import time as _t
                with open(PROPOSED_DIR / ".service_redirects.jsonl", "a", encoding="utf-8") as _fh:
                    _fh.write(json.dumps({"ts": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
                                          "task": d.get("task", ""), "output_file": d.get("output_file", "")}) + "\n")
            except Exception:
                pass
            return False, (
                "HTTP-surface module proposed as a single FILE -- that is the retired "
                "unit (559 built, 16 load-bearing). Re-propose this ONCE as the SERVICE "
                "UNIT: propose_directive(task='build_service_<snake_name>', "
                "handler='build_service', description=<full spec: route+prefix, "
                "tables/columns read, response shape, ACCEPTANCE assertions>). "
                "No output_file. The pipeline fans it out, builds each file, and mounts "
                "it only when its contract proves a live 200.")
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
        if _graph_has_module(output.rsplit("/", 1)[-1]):
            return False, (
                f"already built (live code graph): {output}. Do NOT re-propose this "
                f"module -- it already exists. Propose a NEW capability in an "
                f"underbuilt domain (call list_domains and pick a thin/uncovered one) "
                f"or a concrete integration gap. Re-proposing a built module wastes the cycle.")
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


# --- read-only code-graph tools (graphify) -------------------------------------
# The directive architect PROPOSES; it must NOT execute -- so we expose only the
# graph READ surface here (mirroring builder_mcp's code_nodes/code_edges queries
# via write_service:8772), NOT builder_mcp's build-execution tools. These let the
# architect range across the app's domains and check a module isn't already wired,
# instead of working blind off a flat already_built name list.

def _graph_query(sql: str, timeout: int = 8):
    """Read-only code-graph query via write_service /query (POST). Returns a list
    of row dicts, or [] on ANY error (graph not seeded / bus down) so the architect
    degrades gracefully and proceeds without it."""
    try:
        import requests
        r = requests.post("http://127.0.0.1:8772/query",
                          json={"sql": sql}, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("rows", []) if isinstance(data, dict) else (data or [])
    except Exception as e:
        _log(f"_graph_query error: {e}")
        return []


def _graph_has_module(basename: str) -> bool:
    """True if a .py module with this basename already has nodes in the live code
    graph (code_nodes) -- i.e. ALREADY BUILT per the FRESH graph, even when the
    static ALREADY_BUILT set (which goes stale) omits it. Lets _validate reject the
    freshly-built modules the architect otherwise re-proposes forever (the +0
    fixation). Fail-open: [] / error -> False so a graph hiccup never blocks a
    legitimate proposal."""
    if not basename:
        return False
    b = str(basename).replace("'", "''")
    rows = _graph_query(
        "SELECT 1 FROM code_nodes "
        "WHERE regexp_replace(source_file, '^.*/', '') = "
        f"'{b}' LIMIT 1")
    return bool(rows)


@mcp.tool()
def list_domains(limit: int = 30) -> dict:
    """The app's DOMAIN MAP from the code graph: Leiden communities = clusters of
    related modules (the app's domains). Call this to RANGE ACROSS the app instead
    of fixating on a few subjects -- favour domains that are thin/underbuilt, or
    concrete gaps WITHIN large domains. Each row: community id, module count, an
    example module. Read-only."""
    sql = ("SELECT community, COUNT(*) AS modules, "
           "MIN(regexp_replace(source_file, '^.*/', '')) AS example "
           "FROM code_nodes WHERE source_file LIKE '%.py' "
           "AND source_file NOT LIKE 'directives/%' "
           "AND source_file NOT LIKE 'directives_archive/%' "
           f"GROUP BY community ORDER BY modules DESC LIMIT {int(limit)}")
    rows = _graph_query(sql)
    return {"status": "ok" if rows else "empty",
            "count": len(rows), "domains": rows}


@mcp.tool()
def graph_neighbors(target: str) -> dict:
    """Code-graph neighbourhood of a module/symbol: what it DEPENDS ON and what
    DEPENDS ON IT (code_nodes/code_edges). Call this BEFORE proposing a CREATION
    directive to confirm the module (or its wiring) does not already exist, and to
    find integration gaps to target. `target` = a file/path fragment or symbol
    (e.g. 'api_gateway.py' or 'register_build'). Read-only; [] if not seeded."""
    t = str(target).replace("'", "''")
    tl = t.lower()
    deps = _graph_query(
        "SELECT DISTINCT e.relation AS rel, n2.label AS name, n2.source_file AS file "
        "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
        f"WHERE (n1.source_file LIKE '%{t}%' OR n1.norm_label LIKE '%{tl}%') "
        "AND e.relation <> 'rationale_for' ORDER BY e.relation LIMIT 40")
    dependents = _graph_query(
        "SELECT DISTINCT e.relation AS rel, n1.label AS name, n1.source_file AS file "
        "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
        f"WHERE (n2.source_file LIKE '%{t}%' OR n2.norm_label LIKE '%{tl}%') "
        "AND e.relation <> 'rationale_for' ORDER BY e.relation LIMIT 40")
    return {"status": "ok", "target": target,
            "depends_on": deps, "depended_on_by": dependents,
            "exists_in_graph": bool(deps or dependents)}


def _record_proposal(d: dict) -> None:
    """Best-effort: log this proposal to mesh_memory (memory_type=directive_proposed)
    via the SINGLE writer, so the architect sees its OWN recent proposal history next
    cycle -- including proposals later REJECTED/dropped that no longer sit in proposed/
    -- and stops re-circling the same subjects. NEVER raises: a telemetry write must
    never block a proposal. Routes through write_service POST /write (no new DB conn)."""
    try:
        import requests
        row = {
            "agent_id":    "directive_architect",
            "memory_type": "directive_proposed",
            "content":     json.dumps({"task": d.get("task"),
                                       "output_file": d.get("output_file"),
                                       "complexity": d.get("complexity")}),
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }
        requests.post("http://127.0.0.1:8772/write",
                      json={"table": "mesh_memory", "rows": [row], "wait": False},
                      timeout=5)
    except Exception as e:
        _log(f"_record_proposal (non-fatal): {e}")


@mcp.tool()
def propose_directive(
    task: str,
    handler: str,
    description: str,
    output_file: str = "",
    complexity: str = "medium",
    phase: str | int | float | None = None,
    priority: float | int | str | None = None,
    rationale: str | None = None,
    reads: list | str | None = None,
    recipe: str | None = None,
    next_directive: dict | str | None = None,
) -> dict:
    """Validate and write a directive JSON to directives/proposed/.

    output_file: the NEW file the task creates -- REQUIRED for creation tasks
    (build_*, etc.). LEAVE EMPTY for edit-class tasks (wire_/rewire_/integrate_/
    ...) that modify EXISTING files: they create no new file and are verified by
    process success + their own smoke-test. Stamping a bogus output_file=<task>.py
    on an edit task makes the ghost-guard fail it forever.

    SERVICE UNIT (preferred for ANY new API surface/feature): handler="build_service",
    task="build_service_<snake_name>", output_file EMPTY, description = the FULL service
    spec (route + prefix, tables/columns read, response shape, acceptance assertions).
    The pipeline fans it out into single-file builds and only mounts the service when
    its own contract proves a live 200 -- wiring is no longer your concern.

    Returns: {"status": "written"|"duplicate"|"rejected", "path": str?, "reason": str?}
    """
    # Edit-class tasks never create a <task>.py -- drop any output_file the model
    # supplied so the directive is honest and the ghost-guard trusts process
    # success (build_completion.is_edit_task does the same defensively).
    # LLM-tolerant coercion (2026-07-15): models emit phase as a JSON int and
    # priority as int/str, and add reads/recipe/next_directive copied from the
    # directive JSON example in context_json. FastMCP's pydantic layer rejected
    # those calls BEFORE this function ran ('Input should be a valid string'),
    # goose swallowed the error into model context, the model apologised, and
    # the cycle scored +0 -- which the daemon then MISLABELLED as 'did NOT
    # reach propose_directive'. A bridge that rejects the caller's dialect on
    # a representational nit is a bridge to nowhere: accept, coerce, and
    # ignore the placebo fields (recipe is stamped server-side per the
    # exemplar doctrine; reads is a placebo; next_directive is not honoured).
    phase = str(phase) if phase is not None else None
    try:
        priority = float(priority) if priority is not None else None
    except (TypeError, ValueError):
        priority = None
    del reads, recipe, next_directive   # tolerated, never trusted

    if _is_edit_task(task):
        output_file = ""
    if handler == "build_service":
        output_file = ""   # a service declares no single file; the fan-out children do
    d = {
        "task": task,
        "handler": handler,
        "output_file": output_file or None,
        "complexity": complexity,
        "description": description,
        "source": "directive_architect",
        "proposed_at": datetime.now(timezone.utc).isoformat(),
    }
    # ANTI-HOLLOW ENFORCEMENT (2026-06-28): bind CREATION directives to the validated
    # module_from_exemplar lane deterministically. The directive_architect recipe asks
    # the model to set recipe=module_from_exemplar, but the weak MiniMax architect ignores
    # the prose (the 2026-06-27 ghost builds routed to the weaker FastAPI lane). A creation
    # directive declares an output_file and is not an edit/breaker task; module_from_exemplar
    # defaults its exemplar_file to verdict_breakdown_api.py, so no exemplar arg is needed.
    if output_file and not _is_edit_task(task) and not d.get("recipe"):
        d["recipe"] = "module_from_exemplar"
    if phase is not None:
        d["phase"] = phase
    if priority is not None:
        d["priority"] = priority
    if rationale is not None:
        d["rationale"] = rationale

    if handler == "build_service":
        d["service_name"] = task[len("build_service_"):] if task.startswith("build_service_") else ""
    ok, reason = _validate(d)
    if not ok:
        _log(f"REJECT {task}: {reason}")
        return {"status": "rejected", "reason": reason}

    if _already_done(d.get("directive_id") or task, task):
        _log(f"ALREADY-DONE {task}: done sentinel (or version-collapsed base) exists; not re-proposing")
        return {"status": "duplicate", "reason": "already built (done sentinel)", "task": task}

    # Diagnostic cap: investigate_/diagnose_ are diagnostic, not net-new build work
    # (the recipe wants the MAJORITY net-new; breakers capped at 1/cycle). Without a
    # cap the architect emits investigate_X_v2..v11 loops. Cap them per cycle so it
    # must propose a FIX, not endless investigations.
    global _diag_count
    if task.startswith(_DIAG_PREFIXES):
        if _diag_count >= _DIAG_CAP:
            _log(f"DIAG-CAP {task}: investigate/diagnose cap {_DIAG_CAP}/cycle reached; rejecting")
            return {"status": "rejected",
                    "reason": f"diagnostic cap {_DIAG_CAP}/cycle reached -- propose a FIX (build_/wire_/fix_), not another investigation"}
        _diag_count += 1

    key = hashlib.md5(task.encode()).hexdigest()[:8]
    fname = f"gen_{key}_{task[:35]}.json"
    path = PROPOSED_DIR / fname

    if path.exists():
        _log(f"DUPLICATE {task}: {path} already exists; not overwriting")
        return {"status": "duplicate", "path": str(path), "task": task}

    try:
        path.write_text(json.dumps(d, indent=2))
        _record_proposal(d)
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
