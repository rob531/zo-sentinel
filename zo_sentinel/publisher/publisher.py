"""
publisher.py -- watch build_artifact rows, open a gated PR per new artifact.

Flow (per build_artifact mesh row):
  1. dedup     -- skip artifacts already published (dedup_key in mesh state)
  2. resolve   -- read the artifact's source (host file, or injected in tests)
  3. pre-gate  -- static safety scan (no DROP/DELETE on protected core tables);
                  the DEEP validation is the PR's own E2E gates (smoke/ruff/fe)
  4. plan      -- branch + title + body carrying goose provenance (tier/task)
  5. publish   -- via GitOps (FakeGitOps dry-run when dormant; CliGitOps live)

Dormant by design: when not enabled, run_once() returns the plans it WOULD
publish (action="dry_run") and writes nothing. Enabled only when
`.pr_publisher_enabled` exists or PR_PUBLISHER_ENABLED is truthy.

Rate governance (the repo is PRIVATE -- 2000 GitHub Actions min/month, and every
PR fires pr-gates.yml on hosted runners ~8-10 min each):
  - WATERMARK   -- reads only build_artifacts with created_at > watermark, via the
                   store's read_build_artifacts_since (the plain read ignores the
                   bound and would replay the oldest window forever). Advances the
                   watermark as it goes, so it never re-scans the backlog. Seed it
                   to "now" before enabling (tools/seed_publisher_watermark.py) so
                   the historical backlog is skipped entirely.
  - DAILY CAP   -- at most `daily_cap` PRs per UTC day (default 8 -> ~1900 Actions
                   min/month). Cap-deferred artifacts are NOT skipped: the
                   watermark is not advanced past them, so they publish next day.
  - PR SPACING  -- a sleep between PRs so a burst doesn't trip GitHub's secondary
                   (abuse) rate limits. GitOps adds Retry-After backoff on top.

Reuses zo_sentinel.ingestor: the MeshStore seam, BuildArtifact, classify(),
and static_safety_scan().
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from zo_sentinel.ingestor.contracts import static_safety_scan
from zo_sentinel.ingestor.model import BuildArtifact
from zo_sentinel.ingestor.store import MeshStore
from zo_sentinel.publisher.gitops import FakeGitOps, GitOps, PublishPlan

PUBLISHER_AGENT_ID = "zo_sentinel.pr_publisher"
PR_PUBLISHED_TYPE = "pr_published"          # mesh row holding the published dedup_keys
WATERMARK_TYPE = "pr_publish_watermark"     # highest built_at scanned (ISO string)
BUDGET_TYPE = "pr_publish_budget"           # {"day": "YYYY-MM-DD", "count": N}
SENTINEL_NAME = ".pr_publisher_enabled"
DEFAULT_HOME = "/home/workspace/zo_sentinel"
DEFAULT_BRANCH_PREFIX = "auto/build"
DEFAULT_LABEL = "autonomous-build"
DEFAULT_DAILY_CAP = 8                        # ~8 PRs/day * ~8 Actions-min stays < 2000/mo
DEFAULT_PR_SPACING_SEC = 5.0


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower()
    return (s[:n] or "artifact").strip("-")


def _max_iso(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The later of two ISO timestamps (lexicographic == chronological for ISO);
    tolerates either being None/empty."""
    if not b:
        return a
    if not a or b > a:
        return b
    return a


class Publisher:
    def __init__(self, store: MeshStore, gitops: Optional[GitOps] = None,
                 home: str = DEFAULT_HOME,
                 content_resolver: Optional[Callable[[BuildArtifact], Optional[str]]] = None,
                 repo_url: str = "https://github.com/rob531/zo-sentinel",
                 enabled_override: Optional[bool] = None,
                 daily_cap: int = DEFAULT_DAILY_CAP,
                 pr_spacing_sec: float = DEFAULT_PR_SPACING_SEC,
                 clock: Optional[Callable[[], datetime]] = None,
                 sleep: Optional[Callable[[float], None]] = None):
        self.store = store
        self.gitops = gitops or FakeGitOps(repo_url)
        self.home = Path(home)
        self.repo_url = repo_url.rstrip("/")
        self._resolver = content_resolver or self._read_from_home
        self._enabled_override = enabled_override
        self.daily_cap = max(0, int(daily_cap))
        self.pr_spacing_sec = max(0.0, float(pr_spacing_sec))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep

    # --- dormancy -----------------------------------------------------------
    def is_enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        env = os.environ.get("PR_PUBLISHER_ENABLED")
        if env is not None:
            return env.strip().lower() in ("1", "true", "yes", "on")
        return (self.home / SENTINEL_NAME).exists()

    # --- content resolution -------------------------------------------------
    def _read_from_home(self, art: BuildArtifact) -> Optional[str]:
        """Default resolver: read the built file off the host. Path may be
        absolute or relative to the sentinel home."""
        p = Path(art.file)
        if not p.is_absolute():
            p = self.home / art.file
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    # --- dedup state --------------------------------------------------------
    def _already_published(self) -> set:
        raw = self.store.read_latest(PR_PUBLISHED_TYPE, PUBLISHER_AGENT_ID)
        if not raw:
            return set()
        try:
            data = json.loads(raw)
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, TypeError):
            return set()

    # --- watermark + daily budget ------------------------------------------
    def _write_durable(self, table: str, row: dict, attempts: int = 3) -> bool:
        """store.write, retried a few times. The state writes (watermark / dedup
        / budget) MUST land or the publisher loses its place and re-attempts
        already-open PRs next cycle -- and write_service (:8772) drops writes
        intermittently. store.write returns False on a dropped write; retry."""
        for i in range(max(1, attempts)):
            if self.store.write(table, row):
                return True
            if i + 1 < attempts:
                self._sleep(1.0)   # let a flaky write_service recover
        # All attempts failed. Surface WHY -- these writes were failing silently,
        # so the watermark never persisted while the publisher kept publishing.
        # store.last_error (set by HttpMeshStore._post) is the actual reason.
        sys.stderr.write(
            f"[publisher] WARN: durable write to {table} "
            f"(memory_type={row.get('memory_type')}) failed after {attempts} attempts: "
            f"{getattr(self.store, 'last_error', '?')}\n")
        sys.stderr.flush()
        return False

    def _load_watermark(self) -> Optional[str]:
        return self.store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) or None

    def _save_watermark(self, value: str) -> None:
        self._write_durable("mesh_memory", {
            "agent_id": PUBLISHER_AGENT_ID,
            "memory_type": WATERMARK_TYPE,
            "content": value,
            "importance": 0.3,
        })

    def _load_budget(self) -> tuple:
        """(day, count) for the persisted daily budget; ('', 0) if none."""
        raw = self.store.read_latest(BUDGET_TYPE, PUBLISHER_AGENT_ID)
        if not raw:
            return "", 0
        try:
            d = json.loads(raw)
            return str(d.get("day", "")), int(d.get("count", 0) or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            return "", 0

    def _save_budget(self, day: str, count: int) -> None:
        self._write_durable("mesh_memory", {
            "agent_id": PUBLISHER_AGENT_ID,
            "memory_type": BUDGET_TYPE,
            "content": json.dumps({"day": day, "count": count}),
            "importance": 0.3,
        })

    # --- planning -----------------------------------------------------------
    @staticmethod
    def _tier_of(raw) -> str:
        """Goose provenance: which ladder tier/backend produced this artifact.
        build_artifact rows don't carry it yet (populated once goose_runner
        records the recipe alias / x_zo_task); read defensively."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = {}
        if not isinstance(raw, dict):
            return "unknown"
        return str(raw.get("tier") or raw.get("task_tier")
                   or raw.get("backend") or raw.get("model") or "unknown")

    def plan(self, art: BuildArtifact, content: str, tier: str = "unknown") -> PublishPlan:
        branch = f"{DEFAULT_BRANCH_PREFIX}/{_slug(art.task or art.file)}-{_slug(art.built_at, 16)}"
        title = f"build: {art.task or art.file}"
        body = (
            f"Autonomous build artifact published for E2E gating.\n\n"
            f"- **file**: `{art.file}`\n"
            f"- **task**: {art.task or '(none)'}\n"
            f"- **phase**: {art.phase or '(none)'}\n"
            f"- **interface**: {art.interface or '(none)'}\n"
            f"- **built_at**: {art.built_at or '(none)'}\n"
            f"- **bytes**: {art.bytes}\n"
            f"- **ladder tier**: {tier}\n\n"
            f"This PR runs the standard E2E gates (ruff / smoke-ladder / frontend). "
            f"Opened by `{PUBLISHER_AGENT_ID}`.\n"
        )
        labels = [DEFAULT_LABEL]
        if tier and tier != "unknown":
            labels.append(f"ladder:{_slug(tier, 24)}")
        return PublishPlan(branch=branch, title=title, body=body,
                           file_path=art.file, content=content,
                           dedup_key=art.dedup_key, labels=labels)

    # --- main pass ----------------------------------------------------------
    def run_once(self, limit: int = 20) -> List[dict]:
        enabled = self.is_enabled()
        published = self._already_published()
        watermark = self._load_watermark()

        # Daily budget (UTC day). Reset the in-memory count when the day rolls,
        # so the cap is per-calendar-day. Dormant dry-runs don't consume budget.
        today = self._clock().strftime("%Y-%m-%d")
        bud_day, bud_count = self._load_budget()
        if bud_day != today:
            bud_count = 0
        remaining = max(0, self.daily_cap - bud_count)

        results: List[dict] = []
        new_keys: List[str] = []
        advance_wm = watermark
        published_now = 0

        for row_id, raw, created_at in self.store.read_build_artifacts_since(watermark, limit):
            art = BuildArtifact.from_mesh_content(raw, row_id)
            if art is None:
                continue
            if art.dedup_key in published:
                advance_wm = _max_iso(advance_wm, created_at)   # already done; skip past it
                continue
            content = self._resolver(art)
            if not content:
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "skip", "detail": "content unresolved/empty"})
                advance_wm = _max_iso(advance_wm, created_at)
                continue
            safety = static_safety_scan(content)
            if safety:
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "blocked", "detail": safety})
                advance_wm = _max_iso(advance_wm, created_at)
                continue
            tier = self._tier_of(raw)
            plan = self.plan(art, content, tier)
            if not enabled:
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "dry_run", "branch": plan.branch, "tier": tier})
                continue
            if remaining <= 0:
                # Daily cap hit. Do NOT advance the watermark past this artifact
                # (or it'd be lost); stop so the next day resumes from here.
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "deferred_cap",
                                "detail": f"daily cap {self.daily_cap} reached"})
                break
            res = self.gitops.publish(plan)
            noop = bool(getattr(res, "noop", False))
            action = "noop" if (res.ok and noop) else ("published" if res.ok else "failed")
            results.append({"dedup_key": art.dedup_key, "file": art.file,
                            "action": action,
                            "pr_url": res.pr_url, "detail": res.detail, "tier": tier})
            if not res.ok:
                # Publish failed (network / rate-limit even after GitOps backoff).
                # Stop and do NOT advance past it, so we retry it next cycle.
                break
            # Dedup + advance past this artifact whether it opened a PR or was a
            # no-op (content already on base). A no-op MUST advance too -- otherwise
            # an artifact goose rebuilt byte-identically head-of-line blocks the
            # queue forever (the bug that stalled every PR behind OPERATIONS.md).
            new_keys.append(art.dedup_key)
            advance_wm = _max_iso(advance_wm, created_at)
            if noop:
                # Nothing was opened: don't consume a daily-cap slot or PR spacing.
                continue
            remaining -= 1
            bud_count += 1
            published_now += 1
            if self.pr_spacing_sec and remaining > 0:
                self._sleep(self.pr_spacing_sec)

        if enabled:
            if new_keys:
                self._write_durable("mesh_memory", {
                    "agent_id": PUBLISHER_AGENT_ID,
                    "memory_type": PR_PUBLISHED_TYPE,
                    "content": json.dumps(sorted(published | set(new_keys))),
                    "importance": 0.3,
                })
                self.store.write("audit_log", {
                    "event_type": "PR_PUBLISHED",
                    "actor": PUBLISHER_AGENT_ID,
                    "outcome": "ok",
                    "details_json": json.dumps({"count": len(new_keys), "keys": new_keys}),
                })
            if published_now:
                self._save_budget(today, bud_count)
            # advance_wm only ever covers rows we published or definitively
            # skipped -- never a cap-deferred/failed row (we break before
            # updating it), so those re-surface on the next pass.
            if advance_wm and advance_wm != watermark:
                self._save_watermark(advance_wm)
        return results
