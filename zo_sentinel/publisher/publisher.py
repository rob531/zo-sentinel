"""
publisher.py -- the PRODUCER publishes its own builds through git (RCA 2026-10 Fix 3).

WHAT CHANGED, AND WHY (RCA cross-cycle-2026-10, fault F3)
---------------------------------------------------------
The component that wrote host state was not the one that versioned it.
goose_runner.py wrote generated files into the runtime tree and had ZERO git
call sites; this module was a SEPARATE path that read build_artifact rows out
of the mesh store behind a WATERMARK and pushed each one as a PR. Six outcomes
(hollow_blocked / duplicate_module / saturated_family / blocked / quarantined /
"content unresolved") advanced the watermark and were never retried -- and
every one left the file sitting in the runtime tree, unversioned, forever.
Measured 2026-09-27 (FU-555): host services/active 504 vs origin/main 42, 462
(92%) missing, oldest 63 days; the spine is generated from that host tree.

So the watermark path is RETIRED (removed, not wrapped). In its place one owner,
ProducerCommit, called by the producer at the moment a build completes:

  1. enqueue    -- a durable LOCAL outbox entry (outside the git tree, survives
                   `git clean`; never the write_service mesh store, which drops
                   writes). Nothing is "advanced past": an entry leaves the outbox
                   only on a terminal outcome.
  2. refuse     -- the SAME pre-PR rules as before (static safety, hollow
                   scaffold, saturated family, duplicate module). A refused build
                   is EVICTED from the runtime tree: a tracked path is restored to
                   HEAD, an untracked one is moved to the durable quarantine. The
                   bytes are kept for inspection; the runtime tree no longer
                   carries code no gate will ever see. Hollow builds are also
                   parked, exactly as before.
  3. publish    -- the same GitOps seam (CliGitOps in its own clone; never the
                   live tree). published / noop -> done, and the runtime copy is
                   set aside (tracked: restored to HEAD; untracked: moved to the
                   durable in_flight store) so the file re-enters the runtime tree
                   ONLY through the merged ref -- the deploy ff then cannot abort on
                   it, and nothing unmerged runs. A PERMANENT failure ->
                   evicted + quarantined. A TRANSIENT failure stays pending and
                   is retried by drain() -- never dropped.

So for every file the builder writes, the runtime tree ends in one of two
states: on its way to origin/main through a gated PR, or not in the tree.
`tools/staged_repo_reconcile.py` (census) is the detector; `backfill()` feeds the
historical host-only files through the same path.

Dormant by design: unless `.pr_publisher_enabled` exists (or PR_PUBLISHER_ENABLED
is truthy) nothing is published -- builds are enqueued and wait, never lost.
Rate governance (daily cap + PR spacing) is unchanged.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Callable, List, Optional

from zo_sentinel.ingestor.contracts import static_safety_scan
from zo_sentinel.publisher.gitops import GitOps, PublishPlan

PUBLISHER_AGENT_ID = "zo_sentinel.pr_publisher"
SENTINEL_NAME = ".pr_publisher_enabled"
DEFAULT_HOME = "/home/workspace/zo_sentinel"
DEFAULT_BRANCH_PREFIX = "auto/build"
DEFAULT_LABEL = "autonomous-build"
DEFAULT_DAILY_CAP = 500                      # repo is PUBLIC -> GitHub Actions minutes are
                                             # UNLIMITED (the old 8/day cap protected a
                                             # private-repo 2000-min/mo budget, now obsolete).
                                             # Kept finite as a runaway safety valve; the real
                                             # throttle is now PR_SPACING (abuse-rate-limit). Env: PR_PUBLISHER_DAILY_CAP.
DEFAULT_PR_SPACING_SEC = 5.0
DEFAULT_DUP_FILE_WINDOW_DAYS = 3             # same target file re-arriving under a NEW
                                             # dedup_key within this window = duplicate
                                             # directive churn -> skipped, not published.
                                             # Env: PR_DUP_FILE_WINDOW_DAYS. 0 disables.


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower()
    return (s[:n] or "artifact").strip("-")


def _within_days(earlier_iso: Optional[str], later_iso: Optional[str], days: int) -> bool:
    """True when later_iso falls within `days` of earlier_iso. Defensive: any
    unparseable/missing timestamp -> False (publish normally; a rare dup slip
    is cheaper than wrongly blocking a legitimate build)."""
    if not earlier_iso or not later_iso or days <= 0:
        return False
    try:
        a = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
        b = datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((b - a).total_seconds()) <= days * 86400



# --- anti-hollow pre-publish gate (#1450) ------------------------------------
# The RULE lives in zo_sentinel.gates.hollow -- the single definition shared by
# the builder gate, this pre-publish gate and the no-hollow CI gate. It used to
# be restated here; three copies of a regex are three chances for the seams to
# disagree, and a rule the builder satisfies but CI rejects is worse than none.
# Re-exported so callers (and tests) can keep importing it from the publisher.
from zo_sentinel.build_completion import park_directive   # noqa: E402
from zo_sentinel.gates.hollow import hollow_scaffold_scan   # noqa: F401,E402

# Durable quarantine store, OUTSIDE the git tree: `git clean` on a daemon
# respawn wipes untracked sentinels, which un-parks the directive (council
# 2026-06-20). Same path goose_runner parks to.
DURABLE_QUARANTINE_DIR = Path('/home/workspace/zo_sentinel_state/quarantine')


# --- saturated-family gate (2026-07-12) --------------------------------------
# Enforces the council saturation declaration (docs/DESIGN_CVE_EXPANSION_AND_
# INTEGRITY_2026_07_10.md) in CODE -- the prose-only steer did not hold
# (#1441 fleet_exploit_surface_api and #1447 fleet_risk_composition_api merged
# 2026-07-12 despite it; HISTO precedent: reads[] is a placebo, gates are not).
# New members of saturated permutation families are skipped pre-PR. Filename-
# only check, root-level modules only. Env kill-switch: PR_SATURATION_GATE=0.
_SATURATED_FAMILIES = re.compile(
    r"^(fleet_|org_risk_|mcp_risk_tier|server_risk_delta|server_risk_tier|"
    r"axis_top_servers|scoring_trend|server_exemption|cadence_job_runs)"
    r"\w*\.(py|html)$")


def saturated_family_scan(file_path: str) -> Optional[str]:
    """Return a skip reason if a ROOT-LEVEL artifact belongs to a council-
    saturated module family, else None. Value in these areas = wiring/joining
    EXISTING modules, never new permutations."""
    if os.environ.get("PR_SATURATION_GATE", "1").strip().lower() in ("0", "false", "off"):
        return None
    fp = str(file_path or "")
    if "/" in fp:
        return None
    if _SATURATED_FAMILIES.match(fp):
        return (f"saturated family (council_cve_expansion_2026_07_10): {fp} -- "
                f"no new permutations; wire/join existing modules instead")
    return None



DEFAULT_OUTBOX = "/home/workspace/zo_sentinel_state/producer_outbox.json"
BACKFILL_SENTINEL = ".pr_backfill_enabled"
TERMINAL = ("published", "noop", "refused", "quarantined", "lost")


def plan_for(file: str, content: str, *, task: str = "", phase: str = "",
             interface: str = "", built_at: str = "", tier: str = "unknown",
             dedup_key: str = "") -> PublishPlan:
    """The PR a build becomes. Same branch/title/body/labels as the retired path."""
    branch = f"{DEFAULT_BRANCH_PREFIX}/{_slug(task or file)}-{_slug(built_at, 16)}"
    title = f"build: {task or file}"
    body = (
        f"Autonomous build artifact published for E2E gating.\n\n"
        f"- **file**: `{file}`\n"
        f"- **task**: {task or '(none)'}\n"
        f"- **phase**: {phase or '(none)'}\n"
        f"- **interface**: {interface or '(none)'}\n"
        f"- **built_at**: {built_at or '(none)'}\n"
        f"- **bytes**: {len(content.encode('utf-8'))}\n"
        f"- **ladder tier**: {tier}\n\n"
        f"This PR runs the standard E2E gates (ruff / smoke-ladder / frontend). "
        f"Opened by `{PUBLISHER_AGENT_ID}` from the producer's own outbox.\n"
    )
    labels = [DEFAULT_LABEL]
    if tier and tier != "unknown":
        labels.append(f"ladder:{_slug(tier, 24)}")
    return PublishPlan(branch=branch, title=title, body=body, file_path=file,
                       content=content, dedup_key=dedup_key or f"{file}@{built_at}",
                       labels=labels)


class ProducerCommit:
    """The single owner of 'the bytes the builder wrote == the bytes in origin/main'."""

    def __init__(self, gitops: Optional[GitOps] = None, home: str = DEFAULT_HOME,
                 outbox: Optional[str] = None, quarantine_dir: Optional[str] = None,
                 enabled_override: Optional[bool] = None,
                 daily_cap: int = DEFAULT_DAILY_CAP,
                 pr_spacing_sec: float = DEFAULT_PR_SPACING_SEC,
                 clock: Optional[Callable[[], datetime]] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 dup_file_window_days: Optional[int] = None,
                 git: Optional[Callable[..., "subprocess.CompletedProcess"]] = None):
        # gitops=None means "no real clone": entries are ENQUEUED and left pending.
        # There is deliberately no FakeGitOps fallback here -- a fake that reports
        # ok is how the retired path once marked real builds published (2026-06-15).
        self.gitops = gitops
        self.home = Path(home)
        self.outbox = Path(outbox or os.environ.get("PRODUCER_OUTBOX", DEFAULT_OUTBOX))
        self._quarantine_dir = Path(quarantine_dir or DURABLE_QUARANTINE_DIR)
        self._directives_dir = self.home / "directives"
        self._enabled_override = enabled_override
        self.daily_cap = max(0, int(daily_cap))
        self.pr_spacing_sec = max(0.0, float(pr_spacing_sec))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep
        if dup_file_window_days is None:
            try:
                dup_file_window_days = int(os.environ.get(
                    "PR_DUP_FILE_WINDOW_DAYS", DEFAULT_DUP_FILE_WINDOW_DAYS))
            except ValueError:
                dup_file_window_days = DEFAULT_DUP_FILE_WINDOW_DAYS
        self.dup_file_window_days = max(0, int(dup_file_window_days))
        self._git = git or (lambda *a: subprocess.run(
            ["git", "-C", str(self.home), *a], capture_output=True, text=True))

    # --- dormancy -----------------------------------------------------------
    def is_enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        env = os.environ.get("PR_PUBLISHER_ENABLED")
        if env is not None:
            return env.strip().lower() in ("1", "true", "yes", "on")
        return (self.home / SENTINEL_NAME).exists()

    # --- durable outbox (local file, locked; never the mesh store) ----------
    def _lock(self):
        import contextlib
        import fcntl

        @contextlib.contextmanager
        def _cm():
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self.outbox) + ".lock", "a+") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        return _cm()

    def _load(self) -> dict:
        try:
            d = json.loads(self.outbox.read_text(encoding="utf-8")) or {}
        except Exception:
            d = {}
        d.setdefault("entries", {})
        d.setdefault("published_files", {})
        d.setdefault("budget", {"day": "", "count": 0})
        return d

    def _save(self, d: dict) -> None:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.outbox.with_name(self.outbox.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.outbox)

    def _now(self) -> str:
        return self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- the refusal rules (unchanged in substance; one copy) ---------------
    def refusal(self, rel: str, content: str, built_at: str,
                published_files: dict) -> Optional[tuple]:
        seen = published_files.get(rel)
        if seen and _within_days(seen, built_at, self.dup_file_window_days):
            return ("duplicate_module",
                    f"same file published {seen}; window {self.dup_file_window_days}d")
        sat = saturated_family_scan(rel)
        if sat:
            return ("saturated_family", sat)
        safety = static_safety_scan(content)
        if safety:
            return ("blocked", safety)
        hollow = hollow_scaffold_scan(rel, content)
        if hollow:
            return ("hollow_blocked", hollow)
        return None

    # --- eviction: the runtime tree never keeps code no gate will see -------
    def evict(self, rel: str, why: str, bucket: str = "evicted") -> dict:
        src = self.home / rel
        stamp = self._now().replace(":", "")
        dest = self._quarantine_dir / bucket / stamp / rel
        out = {"evicted": False, "restored_to_head": False, "kept_at": None}
        try:
            if src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
                (dest.parent / (dest.name + ".why")).write_text(why, encoding="utf-8")
                out["kept_at"] = str(dest)
            tracked = self._git("ls-files", "--error-unmatch", "--", rel).returncode == 0
            if tracked:
                r = self._git("checkout", "HEAD", "--", rel)
                out["restored_to_head"] = out["evicted"] = (r.returncode == 0)
            elif src.is_file():
                src.unlink()
                out["evicted"] = True
            else:
                out["evicted"] = True     # nothing on disk: already consistent
        except Exception as e:  # never let bookkeeping crash the producer
            out["error"] = repr(e)[:200]
        return out

    # --- entry points -------------------------------------------------------
    def commit(self, rel: str, *, task: str = "", phase: str = "", interface: str = "",
               built_at: str = "", tier: str = "unknown", source: str = "producer") -> dict:
        """Called by the PRODUCER when a build completes. Enqueue, then attempt now."""
        built_at = built_at or self._now()
        with self._lock():
            d = self._load()
            e = d["entries"].get(rel)
            if e and e.get("status") == "pending" and e.get("source") == source:
                e.update(task=task or e.get("task", ""), built_at=built_at, tier=tier)
            else:
                d["entries"][rel] = {"file": rel, "task": task, "phase": phase,
                                     "interface": interface, "built_at": built_at,
                                     "tier": tier, "source": source,
                                     "status": "pending", "attempts": 0,
                                     "enqueued_at": self._now()}
            self._save(d)
        return self._attempt(rel)

    def drain(self, limit: int = 50) -> List[dict]:
        """Retry every pending entry (oldest first). Stops on a transient failure
        or the daily cap -- the entry stays pending; nothing is skipped past."""
        d = self._load()
        pend = sorted((e for e in d["entries"].values() if e.get("status") == "pending"),
                      key=lambda e: e.get("enqueued_at", ""))
        results = []
        for e in pend[:max(0, limit)]:
            r = self._attempt(e["file"])
            results.append(r)
            if r["action"] in ("transient", "deferred_cap", "dormant", "no_clone"):
                break
            if r["action"] == "published" and self.pr_spacing_sec:
                self._sleep(self.pr_spacing_sec)
        return results

    def backfill(self, missing: List[str], limit: int = 20) -> List[dict]:
        """Feed historical host-only files (staged_repo_reconcile census) through
        the SAME path. Refused ones are held (status 'held'), not evicted: deleting
        old host files that may be serving is the chairman's call -- pass them to
        evict_held() once ruled."""
        out = []
        d = self._load()
        for rel in missing:
            if len(out) >= limit:
                break
            if rel in d["entries"]:
                continue
            out.append(self.commit(rel, task="", built_at=self._now(), source="backfill"))
        return out

    def evict_held(self) -> List[dict]:
        out = []
        with self._lock():
            d = self._load()
            for e in d["entries"].values():
                if e.get("status") == "held":
                    ev = self.evict(e["file"], e.get("detail", "held"))
                    e.update(status="refused", eviction=ev, closed_at=self._now())
                    out.append({"file": e["file"], **ev})
            self._save(d)
        return out

    def status(self) -> dict:
        d = self._load()
        counts: dict = {}
        for e in d["entries"].values():
            counts[e.get("status")] = counts.get(e.get("status"), 0) + 1
        return {"outbox": str(self.outbox), "enabled": self.is_enabled(),
                "gitops": type(self.gitops).__name__ if self.gitops else None,
                "counts": counts, "budget": d.get("budget")}

    # --- one attempt on one entry, under the lock ---------------------------
    def _attempt(self, rel: str) -> dict:
        with self._lock():
            d = self._load()
            e = d["entries"].get(rel)
            if not e or e.get("status") != "pending":
                return {"file": rel, "action": (e or {}).get("status", "absent")}
            res = self._attempt_locked(d, e)
            self._save(d)
            return res

    def _close(self, e: dict, status: str, detail: str = "", **kw) -> None:
        e.update(status=status, detail=detail, closed_at=self._now(), **kw)

    def _attempt_locked(self, d: dict, e: dict) -> dict:
        rel = e["file"]
        e["attempts"] = int(e.get("attempts", 0)) + 1
        e["last_attempt"] = self._now()
        p = self.home / rel
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            content = None
        if not content:
            self._close(e, "lost", "file not on disk at publish time -- nothing to "
                        "version and nothing to evict (tree already consistent)")
            return {"file": rel, "action": "lost"}
        why = self.refusal(rel, content, e.get("built_at", ""), d["published_files"])
        if why:
            action, detail = why
            if e.get("source") == "backfill":
                self._close(e, "held", f"{action}: {detail}", refusal=action)
                return {"file": rel, "action": "held", "refusal": action}
            parked = False
            if action == "hollow_blocked" and e.get("task"):
                parked = park_directive(e["task"],
                                        f"producer refused a hollow build: {detail}",
                                        self._clock().isoformat(), self._directives_dir,
                                        self._quarantine_dir)
            ev = self.evict(rel, f"{action}: {detail}")
            self._close(e, "refused", f"{action}: {detail}", refusal=action,
                        eviction=ev, parked=bool(parked))
            return {"file": rel, "action": action, "evicted": ev["evicted"],
                    "parked": bool(parked)}
        if not self.is_enabled():
            return {"file": rel, "action": "dormant"}
        if self.gitops is None:
            e["last_error"] = "no clone dir -- pending until CliGitOps has a clone"
            return {"file": rel, "action": "no_clone"}
        today = self._clock().strftime("%Y-%m-%d")
        bud = d["budget"] if d["budget"].get("day") == today else {"day": today, "count": 0}
        if bud["count"] >= self.daily_cap:
            return {"file": rel, "action": "deferred_cap"}
        plan = plan_for(rel, content, task=e.get("task", ""), phase=e.get("phase", ""),
                        interface=e.get("interface", ""), built_at=e.get("built_at", ""),
                        tier=e.get("tier", "unknown"))
        res = self.gitops.publish(plan)
        if res.ok:
            noop = bool(getattr(res, "noop", False))
            d["published_files"][rel] = e.get("built_at") or self._now()
            if not noop:
                bud["count"] += 1
                d["budget"] = bud
            # The runtime copy steps aside too. It arrives back through the merged
            # ref (deploy ff), so the tree only ever runs bytes a gate has seen --
            # and an untracked copy left here makes that ff ABORT ("untracked
            # working tree files would be overwritten"), the R01 failure that
            # stalled the _tools ff for 9 ticks. Measured by this module's own
            # two-pole test before this line existed.
            ev = self.evict(rel, f"in flight: {res.pr_url or 'noop'}", bucket="in_flight")
            self._close(e, "noop" if noop else "published", res.detail or "",
                        pr_url=res.pr_url, eviction=ev)
            return {"file": rel, "action": "noop" if noop else "published",
                    "pr_url": res.pr_url}
        if getattr(res, "permanent", False):
            ev = self.evict(rel, f"permanent publish failure: {res.detail}")
            self._close(e, "quarantined", res.detail or "", eviction=ev)
            return {"file": rel, "action": "quarantined", "evicted": ev["evicted"]}
        e["last_error"] = (res.detail or "")[:300]
        return {"file": rel, "action": "transient", "detail": res.detail}
