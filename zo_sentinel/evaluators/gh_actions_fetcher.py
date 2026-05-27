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
- Idempotent: keys on `run_id`. Re-running the fetcher on the same
  workflow runs is a no-op (the existing-row guard short-circuits).
- READ-ONLY w.r.t. the GH side. Never re-runs, cancels, or modifies
  workflow runs.
- Dormant: this module is not registered in supervisord by this commit.
  Robin (or a separate scheduled GH workflow — see
  .github/workflows/fetch-failures.yml) invokes it.

SCHEMA
------
Rows are inserted into `mesh_memory` with:
  agent_id    = "gh_actions_evaluator"
  memory_type = "gh_check_failure"
  importance  = 0.7
  content     = json.dumps({
      "run_id":      <int>,           # idempotency key
      "workflow":    <str>,           # workflow name e.g. "evaluator"
      "commit_sha":  <str>,           # head SHA
      "branch":      <str>,           # head branch
      "conclusion":  <str>,           # "failure" | "timed_out" | ...
      "html_url":    <str>,           # link to the run page
      "summary":     <str>,           # short failure summary (junit if available)
      "created_at":  <iso8601 str>,
      "consumed":    false,
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


def _existing_run_ids_via_write_service() -> set[int]:
    """Query mesh_memory for already-recorded run_ids. Returns empty set if
    the write_service is unreachable (caller will then check sqlite directly).
    """
    sql = (
        "SELECT content FROM mesh_memory "
        f"WHERE memory_type = '{MEMORY_TYPE}' "
        "ORDER BY created_at DESC LIMIT 500"
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
    out: set[int] = set()
    for row in rows:
        try:
            c = json.loads(row.get("content", "{}"))
            rid = c.get("run_id")
            if isinstance(rid, int):
                out.add(rid)
        except (json.JSONDecodeError, AttributeError):
            continue
    return out


def _existing_run_ids_via_sqlite() -> set[int]:
    if not MESH_MEMORY_DB.exists():
        return set()
    try:
        conn = sqlite3.connect(str(MESH_MEMORY_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT content FROM mesh_memory WHERE memory_type = ? "
            "ORDER BY created_at DESC LIMIT 500",
            (MEMORY_TYPE,),
        )
        out: set[int] = set()
        for row in cur:
            try:
                c = json.loads(row["content"])
                rid = c.get("run_id")
                if isinstance(rid, int):
                    out.add(rid)
            except (json.JSONDecodeError, KeyError):
                continue
        conn.close()
        return out
    except sqlite3.Error:
        return set()


def existing_run_ids() -> set[int]:
    """Union of run_ids visible via either write path."""
    return _existing_run_ids_via_write_service() | _existing_run_ids_via_sqlite()


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
# Main
# ---------------------------------------------------------------------------


def build_row(run: dict[str, Any], summary: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "run_id":     int(run["databaseId"]),
        "workflow":   run.get("workflowName") or run.get("name") or "",
        "commit_sha": run.get("headSha", ""),
        "branch":     run.get("headBranch", ""),
        "conclusion": run.get("conclusion", ""),
        "html_url":   run.get("url", ""),
        "summary":    summary or run.get("displayTitle", ""),
        "created_at": now,
        "consumed":   False,
    }
    return {
        "agent_id":    AGENT_ID,
        "memory_type": MEMORY_TYPE,
        "content":     json.dumps(payload),
        "importance":  IMPORTANCE,
        "created_at":  now,
    }


def fetch_and_write(repo: str, limit: int, dry_run: bool = False) -> dict[str, Any]:
    """Top-level entry: list recent runs, filter to failures, write new rows.

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
        if (r.get("conclusion") or "").lower() in
           {"failure", "timed_out", "cancelled", "action_required"}
    ]
    seen = existing_run_ids()
    written = 0
    skipped = 0
    errors = 0
    for run in failed:
        rid = int(run["databaseId"])
        if rid in seen:
            skipped += 1
            continue
        summary = fetch_junit_summary(repo, rid)
        row = build_row(run, summary)
        if dry_run:
            sys.stdout.write(json.dumps(row) + "\n")
            written += 1
            continue
        if write_mesh_row(row):
            written += 1
        else:
            errors += 1
    return {
        "status": "ok" if errors == 0 else "partial",
        "examined": len(runs),
        "failed":   len(failed),
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
