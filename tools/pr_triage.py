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

# WALL-CLOCK BUDGET (FU-136). The job running this has `timeout-minutes: 8`, and
# a job KILLED by that timeout is the worst possible outcome: no labels, no
# digest, no step summary, and -- because stdout is a pipe and therefore block
# buffered -- not one line of its own progress. On 2026-07-28T06:11Z and 06:23Z
# this tool burned 8m16s and 8m09s and left literally zero output both times.
# So it now stops ITSELF, early, and always reaches the digest.
DEADLINE_SEC = float(os.environ.get("PR_TRIAGE_DEADLINE_SEC", "390"))  # 6.5 min
_STARTED = time.monotonic()


def _elapsed() -> float:
    return time.monotonic() - _STARTED


def _out_of_time() -> bool:
    return _elapsed() >= DEADLINE_SEC

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


# A quota exhaustion is NOT the same animal as a 502. Exponential backoff in
# seconds cannot outwait an installation rate limit that resets on the hour, so
# it gets recognised separately and reported as a GATE OUTAGE rather than as
# one more flaky call.
_RATE_LIMITED = ("rate limit", "secondary rate", "api rate limit exceeded")


def _is_rate_limited(stderr: str) -> bool:
    e = (stderr or "").lower()
    return any(t in e for t in _RATE_LIMITED)


# An over-budget GraphQL query does not always come back as a tidy "HTTP 504".
# MEASURED against the live API 2026-07-28T04:5xZ: the same combined PR query
# failed three ways in four consecutive attempts --
#   HTTP 504: 504 Gateway Timeout
#   HTTP 504: We couldn't respond to your request in time...
#   stream error: stream ID 1; CANCEL; received from peer
# The third is GitHub killing the HTTP/2 stream rather than answering, and it
# matches NONE of the tokens above it. Classifying that as non-transient is how
# a retry/degrade path silently fails to arm on the very failure it was built
# for -- so the stream-cancel shapes are named explicitly.
_TRANSIENT_GH = ("http 502", "http 503", "http 504", "timed out", "timeout",
                 "couldn't respond", "rate limit", "secondary rate", "eof",
                 "connection reset", "service unavailable", "try again", "bad gateway",
                 "stream error", "received from peer", "connection closed",
                 "unexpected end")


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
_FAIL = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
# A CANCELLED check is a KILL, not a verdict (FU-141). Nothing about the PR was
# judged -- the run was stopped, almost always by this very workflow's
# `concurrency: cancel-in-progress: true` when the next build PR opened. Reading
# it as a failure condemns a PR for our own scheduling. It is PENDING: revisit.
_KILLED = {"CANCELLED"}

# ...and never read our OWN check. `triage` is in every PR's statusCheckRollup,
# so a cancelled sweep left a red `triage` on N PRs, the NEXT sweep read that as
# gate==failure, labelled them `triage:stale`, and auto-merge (which arms only on
# `triage:solid`) never fired -- which grew the backlog, which caused more
# cancellations. A closed positive-feedback loop in which the monitor is the
# outage. Self-exclusion breaks it. Same family as [[a_monitor_can_drift_on_itself]].
_SELF_CHECKS = {"triage", "pr-triage", "triage-solid-sweep"}


def _gate_state(rollup: list) -> str:
    """Reduce a statusCheckRollup list to 'success' | 'failure' | 'pending'.

    Handles both CheckRun (status/conclusion) and StatusContext (state) shapes.
    Our own triage checks are EXCLUDED and CANCELLED counts as pending -- see
    _SELF_CHECKS / _KILLED above for why both are required to break FU-141.
    """
    if not rollup:
        return "pending"  # no checks reported yet
    any_pending = False
    for c in rollup:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or c.get("context") or "").strip().lower()
        if name in _SELF_CHECKS:
            continue  # never grade a PR on our own sweep's exit status
        # CheckRun: in-progress until status == COMPLETED, then look at conclusion
        status = (c.get("status") or "").upper()
        conclusion = (c.get("conclusion") or "").upper()
        state = (c.get("state") or "").upper()
        verdict = conclusion or state
        if status and status != "COMPLETED" and not verdict:
            any_pending = True
            continue
        if verdict in _KILLED:
            any_pending = True  # a kill is not a verdict; re-triage next run
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


def _triage_labels_of(pr: dict):
    """The triage:* labels GitHub ALREADY has on this PR.

    Free: `labels` is in BOTH _FULL_FIELDS and _CHEAP_FIELDS, so it arrives with
    the row and costs no extra call. Returns None when the row carries no
    `labels` key at all -- an UNKNOWN state, which apply_label must handle
    differently from a known-empty one.
    """
    if "labels" not in pr:
        return None
    out = set()
    for lb in (pr.get("labels") or []):
        name = (lb.get("name") if isinstance(lb, dict) else str(lb)) or ""
        if name.startswith(TRIAGE_PREFIX):
            out.add(name)
    return out


def apply_label(repo: str, number: int, bucket: str, current=None) -> int:
    """Converge PR `number` onto exactly one triage bucket. Returns gh calls made.

    THE SCAR (FU-136): this used to cost FOUR sequential `gh pr edit` calls per
    PR, unconditionally -- one add plus three blind removes -- whether or not the
    PR already carried exactly the right label. MEASURED against the live API
    2026-07-28T07:52Z: 4.06s per PR. At 119 open build PRs that is 483s of pure
    subprocess round-trip: over the 8-minute job budget ON ITS OWN, before the
    ~136s the fetch already spends.

    That cost was always there. It was never PAID because every run died in the
    fetch first. Repairing the fetch (#2172) did not create this -- it EXPOSED
    it, and #2172's "76.1s live proof" had timed only the half that had ever run.

    The sweep is idempotent by design: it re-derives the same bucket for the same
    PR every run, so in the steady state EVERY one of those 476 calls was a no-op
    re-asserting a label already present. The current labels are already in hand,
    so:

      * already correct  -> ZERO calls
      * needs a change   -> exactly ONE call, add + removes combined
      * labels unknown   -> the ORIGINAL shape, unchanged

    Combining add and remove into one invocation is safe ONLY because we remove
    exclusively labels we have SEEN on the PR: `gh pr edit --remove-label` errors
    on a label that is not there, and a combined call that errors would lose the
    ADD with it. That is why the unknown-labels branch keeps the old
    separate-calls shape rather than guessing -- cheapen the common path, never
    the correctness.
    """
    target = f"{TRIAGE_PREFIX}{bucket}"

    if current is None:
        others = [f"{TRIAGE_PREFIX}{b}" for b in BUCKETS if b != bucket]
        res = _gh("pr", "edit", str(number), "-R", repo, "--add-label", target)
        if res is None or res.returncode != 0:
            err = res.stderr.strip() if res is not None else "no result from gh"
            print(f"  warn: could not add label to #{number}: {err}", file=sys.stderr)
        for o in others:
            _gh("pr", "edit", str(number), "-R", repo, "--remove-label", o)  # tolerant
        return 1 + len(others)

    stale = sorted(lb for lb in current if lb != target)
    if target in current and not stale:
        return 0  # already converged -- the steady state

    args = ["pr", "edit", str(number), "-R", repo]
    if target not in current:
        args += ["--add-label", target]
    for o in stale:
        args += ["--remove-label", o]
    res = _gh(*args)
    if res is None or res.returncode != 0:
        err = res.stderr.strip() if res is not None else "no result from gh"
        print(f"  warn: could not label #{number}: {err}", file=sys.stderr)
    return 1


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


# ---------------------------------------------------------------------------
# Fetching the open build-PR set
# ---------------------------------------------------------------------------
# `files` and `statusCheckRollup` are per-PR GraphQL CONNECTIONS. Asking for
# both across `--limit 300` builds ONE query whose cost scales with
# (open PRs x changed files x checks per PR). Past a threshold GitHub answers
# HTTP 504 *deterministically* -- and because the entire run hangs off that
# single call, a 504 means ZERO PRs triaged: exactly the total gate outage the
# rate-limit branch below already has a name for.
#
# Exponential backoff cannot fix this. _gh() already retries 504s, and on
# 2026-07-28T03:33Z it spent five attempts and 71s collecting five 504s from
# the same query (FU-129). A query that is over budget is over budget on every
# attempt; it is not intermittently unlucky. Retrying harder only converts a
# fast failure into a slow one.
#
# So: try the combined query first (it is cheap while the backlog is small),
# and on a TRANSIENT failure DEGRADE to a metadata-only list plus per-PR
# hydration of the two expensive fields. Many small queries always fit the
# budget. Cost is O(open PRs) extra calls, paid only when the fast path fails.
_FULL_FIELDS = "number,title,files,labels,mergeable,statusCheckRollup"
_CHEAP_FIELDS = "number,title,labels,mergeable"
_HYDRATE_FIELDS = "files,statusCheckRollup"


def _is_transient(stderr: str) -> bool:
    e = (stderr or "").lower()
    return any(t in e for t in _TRANSIENT_GH)


def fetch_open_build_prs(repo: str):
    """Return (prs, mode, dropped) for every OPEN autonomous-build PR.

    mode is one of:
      "full"         -- one combined query answered it
      "degraded"     -- metadata list + per-PR hydration (the 504 fallback)
      "rate_limited" -- quota exhausted; caller reports a GATE OUTAGE
      "error"        -- non-transient failure; caller fails the run

    `dropped` lists PR numbers that could not be hydrated. They are EXCLUDED
    from `prs` rather than classified on missing data: `files` is what drives
    the dup and scaffold buckets, so a PR judged without it is judged wrongly,
    and a wrong label is worse than no label. Same fail-closed reflex as
    _manifest_is_valid -- a gate that cannot read its subject must not judge it.
    """
    # retries=1, deliberately. THE DEGRADED PATH IS THE RETRY, and a better one:
    # re-asking an over-budget query the default four times cost 71s of an
    # 8-minute job on 2026-07-28T03:33Z and could never have succeeded. One retry
    # still absorbs a genuine one-off blip; the rest was pure loss.
    res = _gh("pr", "list", "-R", repo, "--label", BUILD_LABEL, "--state", "open",
              "--limit", "300", "--json", _FULL_FIELDS, retries=1)
    if res is not None and res.returncode == 0:
        try:
            return json.loads(res.stdout or "[]"), "full", []
        except json.JSONDecodeError as e:
            print(f"ERROR: bad JSON from gh: {e}", file=sys.stderr)
            return [], "error", []

    err = (res.stderr or "").strip() if res is not None else "no result from gh"
    if _is_rate_limited(err):
        return [], "rate_limited", []
    if not _is_transient(err):
        print(f"ERROR: gh pr list failed: {err}", file=sys.stderr)
        return [], "error", []

    print("::warning title=pr-triage degraded::combined PR query failed "
          f"({err[:180]}); retrying as metadata list + per-PR hydration",
          file=sys.stderr)

    cheap = _gh("pr", "list", "-R", repo, "--label", BUILD_LABEL, "--state", "open",
                "--limit", "300", "--json", _CHEAP_FIELDS)
    if cheap is None or cheap.returncode != 0:
        cerr = (cheap.stderr or "").strip() if cheap is not None else "no result from gh"
        if _is_rate_limited(cerr):
            return [], "rate_limited", []
        print(f"ERROR: gh pr list failed on the degraded path too: {cerr}",
              file=sys.stderr)
        return [], "error", []
    try:
        stubs = json.loads(cheap.stdout or "[]")
    except json.JSONDecodeError as e:
        print(f"ERROR: bad JSON from gh: {e}", file=sys.stderr)
        return [], "error", []

    prs: list = []
    dropped: list = []
    for stub in stubs:
        n = stub.get("number")
        one = _gh("pr", "view", str(n), "-R", repo, "--json", _HYDRATE_FIELDS)
        if one is None or one.returncode != 0:
            dropped.append(n)
            continue
        try:
            extra = json.loads(one.stdout or "{}")
        except json.JSONDecodeError:
            dropped.append(n)
            continue
        if "files" not in extra or "statusCheckRollup" not in extra:
            dropped.append(n)
            continue
        merged = dict(stub)
        merged.update(extra)
        prs.append(merged)

    if dropped:
        # Say it out loud. A silently shrunken batch also shrinks the dup
        # supersede map, so a real duplicate can survive this run unlabelled.
        # That is recoverable (the next run re-triages everything); pretending
        # the PR was triaged would not be.
        visible = sorted(x for x in dropped if x is not None)
        print(f"  warn: {len(dropped)} PR(s) left UNTRIAGED this run "
              f"(hydration failed): {visible}", file=sys.stderr)
    return prs, "degraded", dropped


def main() -> int:
    # Line-buffer the streams. Under Actions these are pipes, so the default
    # block buffering means a run killed by the job timeout emits NOTHING --
    # which is exactly how two 8-minute failures on 2026-07-28 became forensic
    # blackouts. The workflow also passes -u; this is the belt to that braces so
    # the tool stays diagnosable wherever it is run from.
    # ...and force UTF-8. The digest is full of emoji bucket headers, and the
    # final `print(digest)` died with UnicodeEncodeError on a cp1252 Windows
    # console the first time this was ever run by hand -- AFTER the issue had
    # been upserted, so the run did its job and then reported a traceback.
    # A tool that only survives on a UTF-8 runner cannot be exercised where the
    # operator actually stands.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
        except Exception:  # pragma: no cover - very old interpreters
            pass
    repo = _repo()
    prs, mode, dropped = fetch_open_build_prs(repo)

    if mode == "rate_limited":
        # Say what actually happened. An unlabelled run is a gate OUTAGE:
        # nothing was triaged, so nothing can auto-merge, and any red `triage`
        # check now sitting on a PR is about this quota -- not about that PR.
        msg = ("TRIAGE GATE OUTAGE: GitHub API quota exhausted -- ZERO PRs "
               "triaged this run. No PR can reach triage:solid, so auto-merge "
               "is stalled until the next successful run. This is not a defect "
               "in any PR carrying a red `triage` check.")
        print(f"::error title=Triage gate outage (rate limit)::{msg}",
              file=sys.stderr)
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1
    if mode == "error":
        return 1

    if not prs:
        if dropped:
            # NOT the same fact as "there is nothing to do". Every PR that
            # exists failed to hydrate, so this run triaged nothing -- report
            # it as the outage it is instead of exiting 0 on an empty list.
            msg = (f"TRIAGE GATE OUTAGE: {len(dropped)} open PR(s) found but "
                   "NONE could be hydrated -- ZERO PRs triaged this run.")
            print(f"::error title=Triage gate outage (hydration)::{msg}",
                  file=sys.stderr)
            print(f"ERROR: {msg}", file=sys.stderr)
            return 1
        print("No open autonomous-build PRs to triage.")
        return 0

    if mode == "degraded":
        print(f"NOTE: ran in DEGRADED mode -- {len(prs)} PR(s) hydrated "
              f"individually, {len(dropped)} left untriaged.", file=sys.stderr)

    ensure_labels(repo)
    exempted: set = set()
    buckets = classify(prs, repo, exempted)
    prs_by_num = {pr["number"]: pr for pr in prs}

    calls = 0
    deferred = []
    for n, b in sorted(buckets.items()):
        if not b:
            print(f"#{n}: (pending checks -- no label this run)")
            continue
        if _out_of_time():
            # Stop WRITING, but never skip the digest. Classification already
            # covers every hydrated PR, so the digest stays complete and correct
            # even when the label writes do not finish. Labelling is idempotent,
            # so the next run simply converges the remainder.
            deferred.append(n)
            continue
        note = " [manifest-exemption]" if n in exempted else ""
        print(f"#{n}: {b}{note}")
        row = prs_by_num.get(n) or {}
        current = _triage_labels_of(row)
        calls += apply_label(repo, n, b, current)
        if n in exempted:
            have = {(lb.get("name") if isinstance(lb, dict) else str(lb))
                    for lb in (row.get("labels") or [])}
            if "labels" not in row or MANIFEST_MARKER_LABEL not in have:
                # Audit trail (FATHER directive 4): every merge taken under the
                # exemption stays separable from an ordinary solid.
                _gh("pr", "edit", str(n), "-R", repo,
                    "--add-label", MANIFEST_MARKER_LABEL)
                calls += 1

    print(f"label writes: {calls} gh call(s) across {len(buckets)} PR(s), "
          f"t={_elapsed():.1f}s", file=sys.stderr)

    digest = build_digest(prs_by_num, buckets, exempted)
    if deferred:
        msg = (f"{len(deferred)} PR(s) classified but NOT relabelled this run -- "
               f"the {DEADLINE_SEC:.0f}s wall-clock budget was spent. The digest "
               "is COMPLETE; only the label writes are behind, and they converge "
               "on the next run.")
        print(f"::warning title=pr-triage label writes deferred::{msg}",
              file=sys.stderr)
        print(f"  warn: {msg} deferred={sorted(deferred, reverse=True)}",
              file=sys.stderr)
        digest += ("\n\n> **Label writes deferred.** " + msg + " Deferred: "
                   + ", ".join(f"#{x}" for x in sorted(deferred, reverse=True)))
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
