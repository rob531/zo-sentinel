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

Reuses zo_sentinel.ingestor: the MeshStore seam, BuildArtifact, classify(),
and static_safety_scan().
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from zo_sentinel.ingestor.contracts import static_safety_scan
from zo_sentinel.ingestor.model import BuildArtifact
from zo_sentinel.ingestor.store import MeshStore
from zo_sentinel.publisher.gitops import FakeGitOps, GitOps, PublishPlan

PUBLISHER_AGENT_ID = "zo_sentinel.pr_publisher"
PR_PUBLISHED_TYPE = "pr_published"          # mesh row holding the published dedup_keys
SENTINEL_NAME = ".pr_publisher_enabled"
DEFAULT_HOME = "/home/workspace/zo_sentinel"
DEFAULT_BRANCH_PREFIX = "auto/build"
DEFAULT_LABEL = "autonomous-build"


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower()
    return (s[:n] or "artifact").strip("-")


class Publisher:
    def __init__(self, store: MeshStore, gitops: Optional[GitOps] = None,
                 home: str = DEFAULT_HOME,
                 content_resolver: Optional[Callable[[BuildArtifact], Optional[str]]] = None,
                 repo_url: str = "https://github.com/rob531/zo-sentinel",
                 enabled_override: Optional[bool] = None):
        self.store = store
        self.gitops = gitops or FakeGitOps(repo_url)
        self.home = Path(home)
        self.repo_url = repo_url.rstrip("/")
        self._resolver = content_resolver or self._read_from_home
        self._enabled_override = enabled_override

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
        results: List[dict] = []
        new_keys: List[str] = []

        for row_id, raw in self.store.read_build_artifacts(None, limit):
            art = BuildArtifact.from_mesh_content(raw, row_id)
            if art is None or art.dedup_key in published:
                continue
            content = self._resolver(art)
            if not content:
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "skip", "detail": "content unresolved/empty"})
                continue
            safety = static_safety_scan(content)
            if safety:
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "blocked", "detail": safety})
                continue
            tier = self._tier_of(raw)
            plan = self.plan(art, content, tier)
            if not enabled:
                results.append({"dedup_key": art.dedup_key, "file": art.file,
                                "action": "dry_run", "branch": plan.branch, "tier": tier})
                continue
            res = self.gitops.publish(plan)
            results.append({"dedup_key": art.dedup_key, "file": art.file,
                            "action": "published" if res.ok else "failed",
                            "pr_url": res.pr_url, "detail": res.detail, "tier": tier})
            if res.ok:
                new_keys.append(art.dedup_key)

        if enabled and new_keys:
            self.store.write("mesh_memory", {
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
        return results
