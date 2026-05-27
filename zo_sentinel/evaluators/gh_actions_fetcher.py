#!/usr/bin/env python3
"""
gh_actions_fetcher.py -- Reverse-feed GitHub Actions failures into mesh_memory.

PART B of the Goose-T2 cheap evaluator loop. The GH Actions workflow
(.github/workflows/evaluator.yml) plays the role of a tower-side Goose-T2:
it runs pytest on every push, naturally producing a pass/fail check.
This script reads those check results back via the `gh` CLI and writes
each failing run as a `gh_check_failure` row into mesh_memory so the
Directive Architect (goose_recipes/directive_architect.yaml) can pick
them up via the `read_failure_history` MCP tool.

DESIGN
------
- Uses `gh` CLI for auth (already keyring-authed on the tower; no token
  in env/files). Falls back to API via `gh api` for richer queries.
- Writes via write_service POST /write to mesh_memory (canonical pattern,
  same as inject_directive.py and mesh_sentinel_reporter.py). Falls back
  to direct sqlite3 if write_service is down.
- Idempotent at PER-STEP granularity: keys on the 3-tuple
  (workflow_run_id, job_id, step_name). A single GH Actions run can have
  multiple jobs, each with multiple failed steps; we emit ONE row per
  failed step so the Directive Architect sees each distinct failure.
  Re-runs of the same workflow that fail the same step are still
  short-circuited; re-runs that fail a DIFFERENT step produce a new row.
- READ-ONLY w.r.t. the GH side. Never re-runs, cancels, or modifies
  workflow runs.
- Dormant: this module is not registered in supervisord by this commit.
  Robin (or a separate scheduled GH workflow -- see
  .github/workflows/fetch-failures.yml) invokes it.

SCHEMA
------
Rows are inserted into `mesh_memory` with:
  agent_id    = "gh_actions_evaluator"
  memory_type = "gh_check_failure"
  importance  = 0.7
  content     = json.dumps({
      "workflow_run_id": <int>,         # part of dedupe key
      "job_id":          <int>,         # part of dedupe key
      "job_name":        <str>,
      "step_name":       <str>,         # part of dedupe key
      "step_number":     <int>,
      "workflow":        <str>,         # workflow name e.g. "evaluator"
      "commit_sha":      <str>,         # head SHA
      "branch":          <str>,         # head branch
      "conclusion":      <str>,         # "failure" | "timed_out" | ...
      "html_url":        <str>,         # link to the job (with step anchor)
      "summary":         <str>,         # step-level failure text
      "created_at":      <iso8601 str>,
      "consumed":        false,
  })

The Directive Architect's read_failure_history tool already queries by
memory_type; we add 'gh_check_failure' to the IN-clause in a sibling
commit so these rows surface.

USAGE
-----
  python3 zo_sentinel/evaluators/gh_actions_fetcher.py \
      --repo rob531/zo-sentinel \
      --limit 25

Exit codes:
  0  Wrote N new rows (N>=0)
  2  gh CLI not authed or not installed
  3  write path entirely unavailable (both write_service and sqlite fallback)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_REPO = os.environ.get("ZO_GH_REPO", "rob531/zo-sentinel")
DEFAULT_LIMIT = int(os.environ.get("ZO_GH_FETCH_LIMIT", "25"))

WRITE_SERVICE_URL = os.environ.get(
    "WRITE_SERVICE_URL", "http://127.0.0.1:8772"
).rstrip("/")
WRITE_TIMEOUT = float(os.environ.get("ZO_WRITE_TIMEOUT", "8"))

# sqlite fallback path mirrors builder_conventions.json:
#   "mesh_memory.db is SQLite (at /home/workspace/Datasets/zo-mesh/mesh_memory.db),
#    NOT DuckDB, and is accessed via direct sqlite3.connect()"
MESH_MEMORY_DB = Path(
    os.environ.get("ZO_MESH_MEMORY_DB", "/home/workspace/Datasets/zo-mesh/mesh_memory.db")
)

AGENT_ID = "gh_actions_evaluator"
MEMORY_TYPE = "gh_check_failure"
IMPORTANCE = 0.7

# Step-level conclusions we treat as failures worth a row.
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_auth_ok() -> bool:
    try:
        r = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def list_recent_runs(repo: str, limit: int) -> list[dict[str, Any]]:
    """Return up to `limit` most recent workflow runs for `repo`.

    Uses `gh run list --json` (cheaper and more stable than gh api). Filters
    are applied client-side so callers can re-use this for non-failure
    queries.
    """
    fields = ",".join([
        "databaseId", "name", "conclusion", "status", "headBranch",
        "headSha", "url", "createdAt", "workflowName", "displayTitle",
    ])
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        "--limit", str(limit),
        "--json", fields,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        sys.stderr.write(f"gh run list failed: {e}\n")
        return []
    if r.returncode != 0:
        sys.stderr.write(f"gh run list rc={r.returncode}: {r.stderr.strip()[:400]}\n")
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"gh run list returned non-JSON: {e}\n")
        return []


def list_failed_steps(repo: str, run_id: int) -> list[dict[str, Any]]:
    """Return a list of failed-step descriptors for a given workflow run.

    Each item:
        {
          "job_id":      <int>,
          "job_name":    <str>,
          "job_url":     <str>,     # html url, useful for #step anchors
          "step_name":   <str>,
          "step_number": <int>,
          "conclusion":  <str>,
        }

    Returns [] on any gh CLI / parsing failure (caller decides whether to
    write a single run-level fallback row).
    """
    cmd = [
        "gh", "run", "view", str(run_id),
        "--repo", repo,
        "--json", "jobs",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        sys.stderr.write(f"gh run view {run_id} failed: {e}\n")
        return []
    if r.returncode != 0:
        sys.stderr.write(
            f"gh run view {run_id} rc={r.returncode}: {r.stderr.strip()[:400]}\n"
        )
        return []
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"gh run view {run_id} non-JSON: {e}\n")
        return []
    return _extract_failed_steps(payload)


def _extract_failed_steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure-Python helper -- separate from the subprocess call so tests can
    feed it canned JSON. Walks payload['jobs'][*]['steps'][*] and yields
    only the steps with a failing conclusion.
    """
    out: list[dict[str, Any]] = []
    jobs = payload.get("jobs") or []
    for job in jobs:
        job_conclusion = (job.get("conclusion") or "").lower()
        # Only walk jobs that themselves failed; skipped/successful job's
        # steps are not interesting even if a sub-step has odd state.
        if job_conclusion not in FAILED_CONCLUSIONS:
            continue
        job_id = job.get("databaseId") or job.get("id")
        job_name = job.get("name") or ""
        job_url = job.get("url") or job.get("html_url") or ""
        steps = job.get("steps") or []
        for step in steps:
            conc = (step.get("conclusion") or "").lower()
            if conc not in FAILED_CONCLUSIONS:
                continue
            try:
                jid = int(job_id) if job_id is not None else 0
            except (TypeError, ValueError):
                jid = 0
            try:
                step_number = int(step.get("number") or 0)
            except (TypeError, ValueError):
                step_number = 0
            out.append({
                "job_id":      jid,
                "job_name":    job_name,
                "job_url":     job_url,
                "step_name":   step.get("name") or "",
                "step_number": step_number,
                "conclusion":  conc,
            })
    return out


def fetch_junit_summary(repo: str, run_id: int) -> str:
    """Best-effort: download the pytest-junit artifact and extract a short
    failure summary. Returns "" on any failure; the row will still be
    written, just without rich detail.
    """
    tmp = Path(os.environ.get("TMP") or os.environ.get("TMPDIR") or "/tmp")
    out_dir = tmp / f"zo_gh_artifact_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["gh", "run", "download", str(run_id),
             "--repo", repo, "--name", "pytest-junit",
             "--dir", str(out_dir)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return ""
        junit = out_dir / "junit.xml"
        if not junit.exists():
            # gh run download may nest under the artifact name dir
            candidates = list(out_dir.rglob("junit.xml"))
            if not candidates:
                return ""
            junit = candidates[0]
        return _summarize_junit(junit)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    finally:
        # leave the temp dir; the caller's tmp dir hygiene takes over
        pass


def _summarize_junit(path: Path) -> str:
    """Extract up to 3 failing test names + first-line messages."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return ""
    root = tree.getroot()
    failures: list[str] = []
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            f = case.find(kind)
            if f is None:
                continue
            name = case.get("classname", "") + "::" + case.get("name", "")
            msg = (f.get("message") or "").splitlines()[0:1]
            failures.append(f"{name}: {msg[0] if msg else kind}")
            if len(failures) >= 3:
                break
        if len(failures) >= 3:
            break
    return " | ".join(failures)


# ---------------------------------------------------------------------------
# mesh_memory write path
# ---------------------------------------------------------------------------


def _key_triple(payload: dict[str, Any]) -> tuple[int, int, str]:
    """Canonical dedupe key extractor. Defensive against missing fields so
    legacy run_id-only rows in mesh_memory don't crash the set lookup."""
    try:
        rid = int(payload.get("workflow_run_id") or payload.get("run_id") or 0)
    except (TypeError, ValueError):
        rid = 0
    try:
        jid = int(payload.get("job_id") or 0)
    except (TypeError, ValueError):
        jid = 0
    step = str(payload.get("step_name") or "")
    return (rid, jid, step)


def _existing_keys_via_write_service() -> set[tuple[int, int, str]]:
    """Query mesh_memory for already-recorded (run_id, job_id, step_name)
    tuples via the write_service /query endpoint.

    NOTE: write_service may not expose a /query endpoint in all deployments
    (it's tower-only and primarily an insert-only path). On endpoint absence
    or any network/parse failure we return an empty set; the sqlite fallback
    is then the source of truth for dedupe. On the write_service-only path
    (no sqlite available) we accept the small risk of duplicate inserts --
    the directive_architect's downstream dedupe-by-content collapses them.
    """
    sql = (
        "SELECT content FROM mesh_memory "
        f"WHERE memory_type = '{MEMORY_TYPE}' "
        "ORDER BY created_at DESC LIMIT 2000"
    )
    try:
        req = urllib.request.Request(
            f"{WRITE_SERVICE_URL}/query",
            data=json.dumps({"sql": sql}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=WRITE_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return set()
    rows = payload.get("rows") or []
    out: set[tuple[int, int, str]] = set()
    for row in rows:
        try:
            c = json.loads(row.get("content", "{}"))
        except (json.JSONDecodeError, AttributeError):
            continue
        out.add(_key_triple(c))
    return out


def _existing_keys_via_sqlite() -> set[tuple[int, int, str]]:
    """sqlite-side dedupe lookup. Uses json_extract so we don't have to
    materialise every row's content blob in Python.

    Note: legacy rows written before this commit only have `run_id` (not
    `workflow_run_id`); for those `json_extract($.workflow_run_id)` is NULL
    and they will appear as (0, 0, "") in the set, which is harmless --
    a new step-level row will not collide with that sentinel.
    """
    if not MESH_MEMORY_DB.exists():
        return set()
    try:
        conn = sqlite3.connect(str(MESH_MEMORY_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT "
            "  COALESCE(json_extract(content, '$.workflow_run_id'), "
            "           json_extract(content, '$.run_id'), 0) AS rid, "
            "  COALESCE(json_extract(content, '$.job_id'), 0)   AS jid, "
            "  COALESCE(json_extract(content, '$.step_name'), '') AS step "
            "FROM mesh_memory WHERE memory_type = ? "
            "ORDER BY created_at DESC LIMIT 2000",
            (MEMORY_TYPE,),
        )
        out: set[tuple[int, int, str]] = set()
        for row in cur:
            try:
                rid = int(row["rid"] or 0)
            except (TypeError, ValueError):
                rid = 0
            try:
                jid = int(row["jid"] or 0)
            except (TypeError, ValueError):
                jid = 0
            out.add((rid, jid, str(row["step"] or "")))
        conn.close()
        return out
    except sqlite3.Error:
        return set()


def _sqlite_has_key(rid: int, jid: int, step: str) -> bool:
    """Targeted single-key sqlite lookup (used when we have a small bucket
    of incoming rows and want to avoid pulling the whole set into memory).
    Falls back to False on any sqlite error so the write path can proceed.
    """
    if not MESH_MEMORY_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(MESH_MEMORY_DB))
        cur = conn.execute(
            "SELECT 1 FROM mesh_memory "
            "WHERE memory_type = ? "
            "  AND COALESCE(json_extract(content, '$.workflow_run_id'), "
            "               json_extract(content, '$.run_id')) = ? "
            "  AND json_extract(content, '$.job_id')   = ? "
            "  AND json_extract(content, '$.step_name') = ? "
            "LIMIT 1",
            (MEMORY_TYPE, rid, jid, step),
        )
        hit = cur.fetchone() is not None
        conn.close()
        return hit
    except sqlite3.Error:
        return False


def existing_keys() -> set[tuple[int, int, str]]:
    """Union of (workflow_run_id, job_id, step_name) tuples visible via
    either write path."""
    return _existing_keys_via_write_service() | _existing_keys_via_sqlite()


def _write_via_write_service(row: dict[str, Any]) -> bool:
    try:
        req = urllib.request.Request(
            f"{WRITE_SERVICE_URL}/write",
            data=json.dumps(
                {"table": "mesh_memory", "rows": [row], "wait": True}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=WRITE_TIMEOUT) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def _write_via_sqlite(row: dict[str, Any]) -> bool:
    if not MESH_MEMORY_DB.parent.exists():
        return False
    try:
        conn = sqlite3.connect(str(MESH_MEMORY_DB))
        # Schema is owned by another component; we INSERT the four canonical
        # columns and let any missing-column / unique-violation propagate so
        # the caller can fall back / surface a useful error.
        conn.execute(
            "INSERT INTO mesh_memory (agent_id, memory_type, content, importance, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                row["agent_id"],
                row["memory_type"],
                row["content"],
                row["importance"],
                row["created_at"],
            ),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as e:
        sys.stderr.write(f"sqlite write failed: {e}\n")
        return False


def write_mesh_row(row: dict[str, Any]) -> bool:
    """Try write_service first, sqlite second. Returns True on success."""
    if _write_via_write_service(row):
        return True
    return _write_via_sqlite(row)


# ---------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------


def build_row(
    run: dict[str, Any],
    step: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    """Build a mesh_memory row for ONE failed step of ONE workflow run.

    `run`  is the dict returned by `list_recent_runs` (run-level metadata).
    `step` is the dict returned by `_extract_failed_steps` (job + step
           metadata).
    `summary` is the step-level failure text -- junit summary if available,
           else a short string built from the step's conclusion + job URL
           with #step anchor.
    """
    now = datetime.now(timezone.utc).isoformat()
    job_url = step.get("job_url") or run.get("url", "")
    # GitHub job pages support #step:<n>:<line> anchors; we don't have a
    # line so the bare #step:<n> works as a deep link.
    step_number = int(step.get("step_number") or 0)
    if job_url and step_number:
        html_url = f"{job_url}#step:{step_number}:1"
    else:
        html_url = job_url or run.get("url", "")

    if not summary:
        summary = (
            f"{step.get('job_name', '')} :: {step.get('step_name', '')} "
            f"-> {step.get('conclusion', '')} ({html_url})"
        ).strip()

    payload = {
        "workflow_run_id": int(run["databaseId"]),
        "job_id":          int(step.get("job_id") or 0),
        "job_name":        step.get("job_name", ""),
        "step_name":       step.get("step_name", ""),
        "step_number":     step_number,
        "workflow":        run.get("workflowName") or run.get("name") or "",
        "commit_sha":      run.get("headSha", ""),
        "branch":          run.get("headBranch", ""),
        "conclusion":      step.get("conclusion") or run.get("conclusion", ""),
        "html_url":        html_url,
        "summary":         summary,
        "created_at":      now,
        "consumed":        False,
    }
    return {
        "agent_id":    AGENT_ID,
        "memory_type": MEMORY_TYPE,
        "content":     json.dumps(payload),
        "importance":  IMPORTANCE,
        "created_at":  now,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fetch_and_write(repo: str, limit: int, dry_run: bool = False) -> dict[str, Any]:
    """Top-level entry: list recent runs, filter to failures, for each
    failed run walk its jobs+steps and write one row per failed step.

    Returns a summary dict so the caller (CLI or scheduled workflow) can
    log a one-line outcome.
    """
    if not _gh_available():
        sys.stderr.write("gh CLI not installed\n")
        return {"status": "no_gh", "written": 0, "skipped": 0}
    if not _gh_auth_ok():
        sys.stderr.write("gh not authed (run `gh auth login`)\n")
        return {"status": "no_auth", "written": 0, "skipped": 0}

    runs = list_recent_runs(repo, limit)
    failed = [
        r for r in runs
        if (r.get("conclusion") or "").lower() in FAILED_CONCLUSIONS
    ]
    seen = existing_keys()
    written = 0
    skipped = 0
    errors = 0
    examined_steps = 0
    for run in failed:
        rid = int(run["databaseId"])
        steps = list_failed_steps(repo, rid)
        if not steps:
            # No granular step info available -- skip this run rather than
            # emit a degraded run-level row (which would not match the new
            # per-step dedupe key). The caller can re-run later when the
            # gh API has the job data.
            continue
        # Junit summary is fetched once per run; we apply it to every
        # failed step from that run (best-effort enrichment).
        junit_summary = fetch_junit_summary(repo, rid)
        for step in steps:
            examined_steps += 1
            key = (rid, int(step.get("job_id") or 0), step.get("step_name") or "")
            if key in seen:
                skipped += 1
                continue
            # Defence-in-depth: even if the in-memory `seen` set missed it
            # (e.g. write_service /query unavailable AND sqlite was just
            # written to in this same loop), do one more targeted lookup.
            if _sqlite_has_key(*key):
                skipped += 1
                seen.add(key)
                continue
            row = build_row(run, step, junit_summary)
            if dry_run:
                sys.stdout.write(json.dumps(row) + "\n")
                written += 1
                seen.add(key)
                continue
            if write_mesh_row(row):
                written += 1
                seen.add(key)
            else:
                errors += 1
    return {
        "status": "ok" if errors == 0 else "partial",
        "examined": len(runs),
        "failed":   len(failed),
        "examined_steps": examined_steps,
        "written":  written,
        "skipped":  skipped,
        "errors":   errors,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--repo", default=DEFAULT_REPO,
                   help=f"GitHub repo (default: {DEFAULT_REPO})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"Max recent runs to examine (default: {DEFAULT_LIMIT})")
    p.add_argument("--dry-run", action="store_true",
                   help="Print rows that would be written; do not write")
    args = p.parse_args(argv)

    result = fetch_and_write(args.repo, args.limit, dry_run=args.dry_run)
    print(json.dumps(result))
    if result["status"] == "no_gh":
        return 2
    if result["status"] == "no_auth":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
