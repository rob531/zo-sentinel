#!/usr/bin/env python3
"""pr_triage.py -- read-only review-assist triage for autonomous-build PRs.

WHY THIS EXISTS:
  The zo-sentinel pipeline reliably *opens* `auto/build/*` PRs but nothing
  merges them -- dozens pile up unmerged (26 open going back to 2026-06-02 at
  the time of writing), a mix of genuinely-solid builds, duplicates, test
  scaffolds, and stale-base orphans. There is deliberately NO auto-merge (the
  builder is quality-capped; machine-generated code must clear a human merge
  gate). The bottleneck is therefore triage time: a human cannot eyeball 26
  mixed-quality PRs quickly.

  This tool does that triage MECHANICALLY and NON-DESTRUCTIVELY. For every OPEN
  PR labelled `autonomous-build` it assigns exactly one `triage:<bucket>` label
  and writes a single ranked digest (Actions step-summary + an upserted tracking
  issue) so a human can merge the `triage:solid` set in minutes.

  It MERGES NOTHING and CLOSES NOTHING. The only writes are label changes and a
  digest comment -- both reversible. This is the council-endorsed (3+1, FATHER
  ruling C) safe alternative to unattended auto-merge into main.

BUCKETS (cascade -- first match wins, so every PR gets exactly one):
  dup      -- another OPEN auto-build PR shares this one's primary changed-file
              path OR build task name AND has a HIGHER number (this one is
              superseded by the newer build).
  scaffold -- every changed file is a test/verify/wire helper, or the whole
              change is a tiny stub (< MIN_SOLID_ADDITIONS added lines).
  stale    -- not mergeable-clean (conflicts) OR a required gate check FAILED.
  solid    -- none of the above AND all gate checks are green AND mergeable.
  (a PR whose checks are still PENDING gets no label this run; it is
   re-evaluated on the next scheduled run / push.)

DEPENDENCIES: stdlib + the `gh` CLI (preinstalled + authenticated on
GitHub-hosted runners via GH_TOKEN). No third-party imports.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BUILD_LABEL = "autonomous-build"
TRIAGE_PREFIX = "triage:"
DIGEST_ISSUE_LABEL = "pr-triage-digest"
MIN_SOLID_ADDITIONS = 12  # changes adding fewer lines than this are stub scaffolds

# Filenames that are helper/scaffold artifacts, not shippable product features.
SCAFFOLD_PREFIXES = ("verify_", "test_", "wire_", "_canary", "canary_")
SCAFFOLD_SUFFIXES = ("_smoke.py", "_test.py", "_integration_smoke.py")

# A SERVICE REGISTRATION MANIFEST is small BY DESIGN -- declarative metadata,
# not logic -- so MIN_SOLID_ADDITIONS is a category error for it. Both
# tools/promote_staged_to_active.py (static gate 1) and tools/generate_spine.py
# ("presence == registration") key off this exact file, so bucketing it as a
# stub makes the whole service permanently unpromotable and unmountable.
# Exempt it from the LINE COUNT only -- and only once its CONTENT resolves the
# keys the promoter blocks on (CofC 3+FATHER 2026-07-27 directive 2:
# validate, never bare path-match).
MANIFEST_RE = re.compile(r"^services/[^/]+/[^/]+/service\.toml$")
MANIFEST_REQUIRED_KEYS = ("name", "import_path")
# Audit marker so every merge taken under the exemption stays separable from
# ordinary solids. Deliberately NOT a triage: bucket -- auto-merge.yml keys on
# the exact label "triage:solid" and must not need editing for this.
MANIFEST_MARKER_LABEL = "manifest-exemption"

BUCKETS = {
    "solid": ("0e8a16", "All gates green, mergeable, not a dup/scaffold -- merge candidate"),
    "dup": ("5319e7", "Superseded by a newer auto-build PR for the same file/task"),
    "scaffold": ("fbca04", "Test/verify/wire helper or tiny stub -- low merge value"),
    "stale": ("b60205", "Base conflicts or a required gate check failed"),
}


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("ERROR: GITHUB_REPOSITORY not set", file=sys.stderr)
        sys.exit(2)
    return repo


_TRANSIENT_GH = ("http 502", "http 503", "http 504", "timed out", "timeout",
                 "couldn't respond", "rate limit", "secondary rate", "eof",
                 "connection reset", "service unavailable", "try again", "bad gateway")


def _gh(*args: str, check: bool = False, retries: int = 4) -> subprocess.CompletedProcess:
    """Run a gh command, capturing output. Retries transient API failures
    (HTTP 5xx / timeouts / rate limits) with exponential backoff so one flaky
    GitHub GraphQL response can't fail the whole triage run. Raises only when
    check=True (after retries are exhausted)."""
    last = None
    for attempt in range(retries + 1):
        try:
            res = subprocess.run(
                ["gh", *args], capture_output=True, text=True, timeout=120
            )
        except subprocess.TimeoutExpired:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if check:
                raise
            return subprocess.CompletedProcess(["gh", *args], 124, "", "gh timed out")
        last = res
        if res.returncode == 0:
            return res
        err = (res.stderr or "").lower()
        if attempt < retries and any(t in err for t in _TRANSIENT_GH):
            time.sleep(2 ** attempt)  # 1, 2, 4, 8s
            continue
        break
    if check and last is not None and last.returncode != 0:
        raise subprocess.CalledProcessError(last.returncode, last.args, last.stdout, last.stderr)
    return last


# ---------------------------------------------------------------------------
# Check / mergeability interpretation
# ---------------------------------------------------------------------------
_PASS = {"SUCCESS", "NEUTRAL", "SKIPPED", "EXPECTED"}
_FAIL = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}


def _gate_state(rollup: list) -> str:
    """Reduce a statusCheckRollup list to 'success' | 'failure' | 'pending'.

    Handles both CheckRun (status/conclusion) and StatusContext (state) shapes.
    """
    if not rollup:
        return "pending"  # no checks reported yet
    any_pending = False
    for c in rollup:
        if not isinstance(c, dict):
            continue
        # CheckRun: in-progress until status == COMPLETED, then look at conclusion
        status = (c.get("status") or "").upper()
        conclusion = (c.get("conclusion") or "").upper()
        state = (c.get("state") or "").upper()
        verdict = conclusion or state
        if status and status != "COMPLETED" and not verdict:
            any_pending = True
            continue
        if verdict in _FAIL:
            return "failure"
        if verdict in _PASS:
            continue
        # PENDING / EXPECTED / blank -> not yet decided
        any_pending = True
    return "pending" if any_pending else "success"


def _task_of(title: str) -> str:
    """Extract the build task from a 'build: <task>' PR title."""
    t = title.strip()
    low = t.lower()
    if low.startswith("build:"):
        return t.split(":", 1)[1].strip()
    return ""


def _primary_path(files: list) -> str:
    """The largest changed file path -- the PR's main artifact."""
    best, best_add = "", -1
    for f in files or []:
        add = f.get("additions", 0) or 0
        if add > best_add:
            best_add, best = add, f.get("path", "")
    return best


def _manifest_only(files: list) -> bool:
    """True when this PR changes exactly one file and it is a service manifest."""
    paths = [f.get("path", "") for f in (files or [])]
    return len(paths) == 1 and bool(MANIFEST_RE.match(paths[0] or ""))


def _manifest_is_valid(repo: str, number: int) -> bool:
    """True only if the manifest this PR ADDS parses as TOML and resolves every
    key promote_staged_to_active.py blocks on.

    Reads the DIFF rather than the head tree, so the thing judged is exactly the
    thing that would land. Fails CLOSED: parse error, missing key, or an
    unreadable diff all return False, dropping the PR back into the ordinary
    scaffold bucket. A gate that cannot read its subject must not bless it.
    """
    res = _gh("pr", "diff", str(number), "-R", repo)
    if res is None or res.returncode != 0:
        return False
    added = "\n".join(
        ln[1:] for ln in (res.stdout or "").splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    )
    if not added.strip():
        return False
    try:
        import tomllib

        meta = tomllib.loads(added).get("service", {})
    except Exception:
        return False
    if not isinstance(meta, dict):
        return False
    return all(str(meta.get(k) or "").strip() for k in MANIFEST_REQUIRED_KEYS)


def _is_scaffold(files: list) -> bool:
    paths = [f.get("path", "") for f in (files or [])]
    if not paths:
        return False
    total_add = sum((f.get("additions", 0) or 0) for f in files)
    if total_add < MIN_SOLID_ADDITIONS:
        return True  # stub
    for p in paths:
        base = p.rsplit("/", 1)[-1]
        is_scaf = base.startswith(SCAFFOLD_PREFIXES) or base.endswith(SCAFFOLD_SUFFIXES) or p.startswith("tests/")
        if not is_scaf:
            return False  # at least one real product file -> not a pure scaffold
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def classify(prs: list, repo: str = "", exempted: set | None = None) -> dict:
    """Return {number: bucket-or-None}. None == leave unlabelled (checks pending).

    `repo` is required to read a manifest PR's diff for schema validation; with
    no repo the manifest exemption simply never arms (fail-closed). Numbers that
    took the exemption are recorded into `exempted` for the audit marker.
    """
    # Build supersede maps: file/task -> highest open PR number using it.
    newest_for_path: dict[str, int] = {}
    newest_for_task: dict[str, int] = {}
    for pr in prs:
        n = pr["number"]
        path = _primary_path(pr.get("files"))
        task = _task_of(pr.get("title", ""))
        if path:
            newest_for_path[path] = max(newest_for_path.get(path, 0), n)
        if task:
            newest_for_task[task] = max(newest_for_task.get(task, 0), n)

    out: dict[int, str | None] = {}
    for pr in prs:
        n = pr["number"]
        path = _primary_path(pr.get("files"))
        task = _task_of(pr.get("title", ""))
        superseded = (path and newest_for_path.get(path, 0) > n) or (
            task and newest_for_task.get(task, 0) > n
        )
        if superseded:
            out[n] = "dup"
            continue
        files = pr.get("files")
        # A single-file service manifest is exempt from the line-count stub rule,
        # but only once its content proves it actually registers something. Mixed
        # PRs (manifest + anything else) stay strict (FATHER directive 3).
        manifest_ok = bool(
            repo and _manifest_only(files) and _manifest_is_valid(repo, n)
        )
        if manifest_ok and exempted is not None:
            exempted.add(n)
        if not manifest_ok and _is_scaffold(files):
            out[n] = "scaffold"
            continue
        mergeable = (pr.get("mergeable") or "").upper()
        gate = _gate_state(pr.get("statusCheckRollup") or [])
        if mergeable == "CONFLICTING" or gate == "failure":
            out[n] = "stale"
            continue
        if gate == "success" and mergeable in ("MERGEABLE", "UNKNOWN", ""):
            out[n] = "solid"
            continue
        out[n] = None  # checks pending -> revisit next run
    return out


def ensure_labels(repo: str) -> None:
    for name, (color, desc) in BUCKETS.items():
        _gh("label", "create", f"{TRIAGE_PREFIX}{name}", "-R", repo,
            "--color", color, "--description", desc, "--force")
    _gh("label", "create", MANIFEST_MARKER_LABEL, "-R", repo,
        "--color", "1d76db",
        "--description",
        "Solid via the service-manifest line-count exemption (schema-validated)",
        "--force")
    _gh("label", "create", DIGEST_ISSUE_LABEL, "-R", repo,
        "--color", "ededed", "--description", "Tracking issue for auto-build PR triage digest", "--force")


def apply_label(repo: str, number: int, bucket: str) -> None:
    others = [f"{TRIAGE_PREFIX}{b}" for b in BUCKETS if b != bucket]
    res = _gh("pr", "edit", str(number), "-R", repo, "--add-label", f"{TRIAGE_PREFIX}{bucket}")
    if res.returncode != 0:
        print(f"  warn: could not add label to #{number}: {res.stderr.strip()}", file=sys.stderr)
    for o in others:
        _gh("pr", "edit", str(number), "-R", repo, "--remove-label", o)  # tolerant


def build_digest(prs_by_num: dict, buckets: dict, exempted: set | None = None) -> str:
    order = ["solid", "stale", "scaffold", "dup"]
    headers = {
        "solid": "✅ SOLID — merge candidates (all gates green, not dup/scaffold)",
        "stale": "⚠️ STALE — base conflict or a gate failed (rebuild or close)",
        "scaffold": "\U0001f9ea SCAFFOLD — test/verify/wire/stub (low merge value, usually close)",
        "dup": "♻️ DUP — superseded by a newer build (close)",
    }
    grouped: dict[str, list] = defaultdict(list)
    for n, b in buckets.items():
        if b:
            grouped[b].append(n)
    lines = ["# \U0001f916 Autonomous-build PR triage digest", ""]
    total = sum(len(v) for v in grouped.values())
    pending = [n for n, b in buckets.items() if b is None]
    lines.append(f"**{total}** triaged · **{len(grouped.get('solid', []))} solid** · "
                 f"{len(grouped.get('stale', []))} stale · {len(grouped.get('scaffold', []))} scaffold · "
                 f"{len(grouped.get('dup', []))} dup"
                 + (f" · {len(pending)} pending-checks (unlabelled)" if pending else ""))
    lines.append("")
    for b in order:
        nums = sorted(grouped.get(b, []), reverse=True)
        lines.append(f"## {headers[b]}  ({len(nums)})")
        if not nums:
            lines.append("_none_")
        for n in nums:
            pr = prs_by_num[n]
            mark = "  `manifest-exemption`" if (exempted and n in exempted) else ""
            lines.append(f"- #{n} — {pr.get('title','').strip()}{mark}")
        lines.append("")
    if pending:
        lines.append("## ⏳ Pending checks (re-evaluated next run): "
                     + ", ".join(f"#{n}" for n in sorted(pending, reverse=True)))
    lines.append("")
    lines.append("_Read-only triage. This bot merges nothing and closes nothing — "
                 "it only labels and reports. Merge the SOLID set; close DUP/SCAFFOLD; "
                 "rebuild or close STALE._")
    return "\n".join(lines)


def upsert_digest_issue(repo: str, body: str) -> None:
    res = _gh("issue", "list", "-R", repo, "--label", DIGEST_ISSUE_LABEL,
              "--state", "open", "--json", "number", "--limit", "1")
    num = None
    if res.returncode == 0 and res.stdout.strip():
        try:
            arr = json.loads(res.stdout)
            if arr:
                num = arr[0]["number"]
        except Exception:
            pass
    if num is None:
        r = _gh("issue", "create", "-R", repo, "--title", "\U0001f916 Autonomous-build PR triage",
                "--label", DIGEST_ISSUE_LABEL, "--body", body)
        if r.returncode != 0:
            print(f"  warn: could not create digest issue: {r.stderr.strip()}", file=sys.stderr)
    else:
        r = _gh("issue", "edit", str(num), "-R", repo, "--body", body)
        if r.returncode != 0:
            print(f"  warn: could not update digest issue #{num}: {r.stderr.strip()}", file=sys.stderr)


def main() -> int:
    repo = _repo()
    res = _gh("pr", "list", "-R", repo, "--label", BUILD_LABEL, "--state", "open",
              "--limit", "300", "--json",
              "number,title,files,labels,mergeable,statusCheckRollup")
    if res.returncode != 0:
        print(f"ERROR: gh pr list failed: {res.stderr.strip()}", file=sys.stderr)
        return 1
    try:
        prs = json.loads(res.stdout or "[]")
    except json.JSONDecodeError as e:
        print(f"ERROR: bad JSON from gh: {e}", file=sys.stderr)
        return 1

    if not prs:
        print("No open autonomous-build PRs to triage.")
        return 0

    ensure_labels(repo)
    exempted: set = set()
    buckets = classify(prs, repo, exempted)
    prs_by_num = {pr["number"]: pr for pr in prs}

    for n, b in sorted(buckets.items()):
        if b:
            note = " [manifest-exemption]" if n in exempted else ""
            print(f"#{n}: {b}{note}")
            apply_label(repo, n, b)
            if n in exempted:
                # Audit trail (FATHER directive 4): every merge taken under the
                # exemption stays separable from an ordinary solid.
                _gh("pr", "edit", str(n), "-R", repo,
                    "--add-label", MANIFEST_MARKER_LABEL)
        else:
            print(f"#{n}: (pending checks — no label this run)")

    digest = build_digest(prs_by_num, buckets, exempted)
    upsert_digest_issue(repo, digest)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(digest + "\n")
        except Exception as e:
            print(f"  warn: could not write step summary: {e}", file=sys.stderr)
    print("\n" + digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
