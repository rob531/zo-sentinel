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
  - DAILY CAP   -- at most `daily_cap` PRs per UTC day (default 100; the repo is
                   PUBLIC so Actions minutes are unlimited -- the cap is now only a
                   runaway safety valve, not a budget limit). Cap-deferred artifacts
                   are NOT skipped: the watermark is not advanced past them, so they
                   publish next day.
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


def _max_iso(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The later of two ISO timestamps (lexicographic == chronological for ISO);
    tolerates either being None/empty."""
    if not b:
        return a
    if not a or b > a:
        return b
    return a



# --- anti-hollow pre-publish gate (2026-07-13) -------------------------------
# Mirrors tests/ci/no_hollow_scaffold.py (the no-hollow CI gate). The builder
# recurringly emits hollow scaffolds -- standalone FastAPI modules with no real
# data layer (the #1438 NVD ingestor wrapped in FastAPI()), or modules carrying
# mock/placeholder text (#1449 inline requests_mock tests). CI rejects them,
# but each one still burned a PR + chairman triage (5 closed 2026-07-13 alone,
# ~50% of that cycle's builder yield). Blocking HERE converts a doomed PR into
# a mesh-visible "hollow_blocked" result. Patterns are kept IDENTICAL to the
# CI gate so this never blocks something CI would accept.
_HOLLOW_MOCK = re.compile(r"class\s+Mock|MockDB|mock database|mock data|placeholder|dummy data|"
                          r"simulate fetching|in-memory (db|database)|# *Mock", re.I)
_HOLLOW_BUILDS_API = re.compile(r"FastAPI\(|APIRouter\(|@app\.(get|post)|@router\.(get|post)")
_HOLLOW_REAL = re.compile(r"from app\.db|from app\.models|import app\.db|app\.models import|"
                          r"get_session|from app import|import verdict_breakdown_api")


def hollow_scaffold_scan(file_path: str, source: str) -> Optional[str]:
    """Return a block reason if a ROOT-LEVEL .py artifact is a hollow scaffold
    (per the no-hollow CI gate), else None. Non-root and non-.py files pass:
    the CI gate only inspects added root-level modules, and this scan must
    stay exactly as permissive."""
    fp = str(file_path or "")
    if "/" in fp or not fp.endswith(".py"):
        return None
    if _HOLLOW_MOCK.search(source):
        return "hollow scaffold: mock/placeholder DB (no-hollow CI would reject)"
    if _HOLLOW_BUILDS_API.search(source) and not _HOLLOW_REAL.search(source):
        return ("hollow scaffold: standalone API with no real data layer "
                "(app.db/app.models) (no-hollow CI would reject)")
    return None


class Publisher:
    def __init__(self, store: MeshStore, gitops: Optional[GitOps] = None,
                 home: str = DEFAULT_HOME,
                 content_resolver: Optional[Callable[[BuildArtifact], Optional[str]]] = None,
                 repo_url: str = "https://github.com/rob531/zo-sentinel",
                 enabled_override: Optional[bool] = None,
                 daily_cap: int = DEFAULT_DAILY_CAP,
                 pr_spacing_sec: float = DEFAULT_PR_SPACING_SEC,
                 clock: Optional[Callable[[], datetime]] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 state_file: Optional[str] = None,
                 dup_file_window_days: Optional[int] = None):
        self.store = store
        # Local durable state file (watermark/dedup/budget). When set it is the
        # AUTHORITATIVE store for the publisher's own bookkeeping so a dropped
        # write_service write can't freeze the watermark (the 2026-06-23 stall).
        # None (default, incl. all unit tests) -> legacy store-only behaviour.
        self._state_file = state_file
        self.gitops = gitops or FakeGitOps(repo_url)
        self.home = Path(home)
        self.repo_url = repo_url.rstrip("/")
        self._resolver = content_resolver or self._read_from_home
        self._enabled_override = enabled_override
        self.daily_cap = max(0, int(daily_cap))
        self.pr_spacing_sec = max(0.0, float(pr_spacing_sec))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep
        if dup_file_window_days is None:
            try:
                dup_file_window_days = int(
                    os.environ.get("PR_DUP_FILE_WINDOW_DAYS",
                                   DEFAULT_DUP_FILE_WINDOW_DAYS))
            except ValueError:
                dup_file_window_days = DEFAULT_DUP_FILE_WINDOW_DAYS
        self.dup_file_window_days = max(0, int(dup_file_window_days))

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

    # --- local durable state (drop-proof; survives git clean) ---------------
    def _state_load(self) -> dict:
        if not self._state_file:
            return {}
        try:
            return json.loads(Path(self._state_file).read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _state_update(self, **kw) -> None:
        if not self._state_file:
            return
        data = self._state_load()
        data.update(kw)
        try:
            p = Path(self._state_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            sys.stderr.write(f"[publisher] WARN: state file write failed: {e}\n")
            sys.stderr.flush()

    # --- dedup state --------------------------------------------------------
    def _published_files(self) -> dict:
        """{file_path: iso_ts} of recently published module files. Guards the
        same-module/two-directives hole (2026-07-10: #1397 + #1398 were both
        `server_risk_delta_timeline_api.py` from two different directives, so
        their dedup_keys differed and both merged). State-file only -- on a
        cold start the map is empty and the guard simply passes; it converges
        after the first publish."""
        if self._state_file:
            _pf = self._state_load().get("published_files")
            if isinstance(_pf, dict):
                return _pf
        return {}

    def _already_published(self) -> set:
        if self._state_file:
            _pub = self._state_load().get("published")
            if _pub is not None:
                return set(_pub)
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
        if self._state_file:
            _wm = self._state_load().get("watermark")
            if _wm:
                return _wm
        return self.store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) or None

    def _save_watermark(self, value: str) -> None:
        self._state_update(watermark=value)
        self._write_durable("mesh_memory", {
            "agent_id": PUBLISHER_AGENT_ID,
            "memory_type": WATERMARK_TYPE,
            "content": value,
            "importance": 0.3,
        })

    def _load_budget(self) -> tuple:
        """(day, count) for the persisted daily budget; ('', 0) if none."""
        if self._state_file:
            _b = self._state_load().get("budget")
            if isinstance(_b, dict):
                try:
                    return str(_b.get("day", "")), int(_b.get("count", 0) or 0)
                except (TypeError, ValueError):
                    pass
        raw = self.store.read_latest(BUDGET_TYPE, PUBLISHER_AGENT_ID)
        if not raw:
            return "", 0
        try:
            d = json.loads(raw)
            return str(d.get("day", "")), int(d.get("count", 0) or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            return "", 0

    def _save_budget(self, day: str, count: int) -> None:
        self._state_update(budget={"day": day, "count": count})
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
        published_files = dict(self._published_files())
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
            # Same-module guard: a DIFFERENT directive rebuilding the SAME file
            # within the window is churn, not an update (byte-identical rebuilds
            # already no-op at gitops; failed builds never enter published_files).
            _seen = published_files.get(art.file)
            if _seen and _within_days(_seen, created_at, self.dup_file_window_days):
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "duplicate_module",
                                "detail": f"same file published {_seen}; "
                                          f"window {self.dup_file_window_days}d"})
                new_keys.append(art.dedup_key)   # never re-attempt this artifact
                advance_wm = _max_iso(advance_wm, created_at)
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
            hollow = hollow_scaffold_scan(art.file, content)
            if hollow:
                # Deterministic on these bytes: retrying the SAME artifact can
                # never pass, so mark + advance past it (mirror the safety
                # "blocked" path). A future FIXED rebuild is a NEW artifact
                # (new built_at/dedup_key) and publishes normally.
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "hollow_blocked", "detail": hollow})
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
                if getattr(res, "permanent", False):
                    # DETERMINISTIC failure (bad path, unwritable, malformed) --
                    # retrying can't fix it. QUARANTINE: mark, advance past it, and
                    # keep going, so one poison artifact can't head-of-line-block
                    # every newer PR forever (the absolute-path stall, 2026-06-08).
                    results[-1]["action"] = "quarantined"
                    advance_wm = _max_iso(advance_wm, created_at)
                    continue
                # Transient (network / rate-limit even after GitOps backoff).
                # Stop and do NOT advance past it, so we retry it next cycle.
                break
            # Dedup + advance past this artifact whether it opened a PR or was a
            # no-op (content already on base). A no-op MUST advance too -- otherwise
            # an artifact goose rebuilt byte-identically head-of-line blocks the
            # queue forever (the bug that stalled every PR behind OPERATIONS.md).
            new_keys.append(art.dedup_key)
            advance_wm = _max_iso(advance_wm, created_at)
            # Remember the target file (noop included: content is on base either
            # way) so a different directive rebuilding it soon gets skipped.
            published_files[art.file] = created_at or self._clock().isoformat()
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
                # Prune the file map so the state file stays bounded: anything
                # older than 10x the guard window can never match again.
                _now = self._clock().isoformat()
                _keep = self.dup_file_window_days * 10 or 30
                published_files = {f: ts for f, ts in published_files.items()
                                   if _within_days(ts, _now, _keep)}
                self._state_update(published=sorted(published | set(new_keys)),
                                   published_files=published_files)
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
