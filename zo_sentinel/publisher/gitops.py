"""
gitops.py -- the git/GitHub seam for the PR publisher.

Mirrors the ingestor's store seam: a Protocol with a hermetic Fake (tests /
dormant dry-run) and a real Cli implementation (a host clone + `git` + `gh`).
The publisher never touches the live working tree -- CliGitOps operates only
inside its own `clone_dir`, and auth is whatever `gh` already holds (an
AgentVault-hydrated token on the host), never a raw env secret.
"""
from __future__ import annotations

import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol

# Substrings GitHub emits when it throttles content creation (push / pr create).
# Hitting these means back off and retry, NOT give up -- a burst of PRs trips the
# secondary (abuse) rate limit even while the primary 5000/hr budget is healthy.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "secondary rate limit",
    "abuse detection",
    "retry-after",
    "api rate limit exceeded",
    "was submitted too quickly",
    "you have exceeded a secondary rate limit",
    "try again later",
    "please wait a few minutes",
)


def _is_rate_limited(text: Optional[str]) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _RATE_LIMIT_MARKERS)


@dataclass
class PublishPlan:
    branch: str
    title: str
    body: str
    file_path: str          # repo-relative path to write the artifact to
    content: str            # the artifact source to commit
    dedup_key: str
    base: str = "main"
    labels: List[str] = field(default_factory=list)


@dataclass
class PublishResult:
    ok: bool
    pr_url: Optional[str] = None
    branch: Optional[str] = None
    detail: str = ""
    # True when there was genuinely nothing to do because the artifact's content
    # already matches base (goose rebuilt an existing file byte-identically) -- a
    # SUCCESS (desired end state reached), not a failure, and NOT a real PR. The
    # publisher dedups + advances past it but does not burn a daily-cap slot.
    noop: bool = False


class GitOps(Protocol):
    def publish(self, plan: PublishPlan) -> PublishResult: ...


class FakeGitOps:
    """Records publish() calls; opens no real PR. Used by tests and by the
    publisher's dormant dry-run so a run can be asserted with no side effects."""

    def __init__(self, base_url: str = "https://github.com/rob531/zo-sentinel"):
        self.base_url = base_url.rstrip("/")
        self.published: List[PublishPlan] = []

    def publish(self, plan: PublishPlan) -> PublishResult:
        self.published.append(plan)
        n = len(self.published)
        return PublishResult(ok=True, pr_url=f"{self.base_url}/pull/FAKE{n}",
                             branch=plan.branch, detail="fake")


class CliGitOps:
    """Real git + gh against a dedicated host clone (NOT the live tree).

    Each publish: fetch base, create the branch off origin/<base>, write the
    file, commit (bot identity), push, `gh pr create`. Every step is checked;
    any failure returns ok=False with detail rather than raising, so a flaky
    network never crashes the publisher daemon.
    """

    def __init__(self, clone_dir: str, repo: str = "rob531/zo-sentinel",
                 remote: str = "origin",
                 author_name: str = "zo-sentinel-bot",
                 author_email: str = "bot@zocomputer.io",
                 max_retries: int = 4,
                 backoff_base_sec: float = 30.0,
                 backoff_cap_sec: float = 600.0,
                 sleep: Optional[Callable[[float], None]] = None,
                 rng: Optional[random.Random] = None):
        self.clone_dir = Path(clone_dir)
        self.repo = repo
        self.remote = remote
        self.author_name = author_name
        self.author_email = author_email
        self.last_error: Optional[str] = None
        # Backoff for GitHub secondary-rate-limit on the network steps.
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_sec = float(backoff_base_sec)
        self.backoff_cap_sec = float(backoff_cap_sec)
        self._sleep = sleep or time.sleep
        self._rng = rng or random.Random()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.clone_dir), *args],
            capture_output=True, text=True,
        )

    def _run_with_backoff(self, fn: Callable[[], subprocess.CompletedProcess]
                          ) -> subprocess.CompletedProcess:
        """Run a network step; on a GitHub rate-limit signal, exponential
        backoff (with jitter) and retry up to max_retries. Non-rate-limit
        failures return immediately (the caller surfaces them as ok=False)."""
        attempt = 0
        while True:
            r = fn()
            if r.returncode == 0:
                return r
            err = (r.stderr or "") + (r.stdout or "")
            if attempt >= self.max_retries or not _is_rate_limited(err):
                return r
            delay = min(self.backoff_cap_sec,
                        self.backoff_base_sec * (2 ** attempt))
            delay += self._rng.uniform(0.0, delay * 0.25)   # jitter, no thundering herd
            self.last_error = (f"rate-limited; backoff {delay:.0f}s "
                               f"(attempt {attempt + 1}/{self.max_retries})")
            self._sleep(delay)
            attempt += 1

    def publish(self, plan: PublishPlan) -> PublishResult:
        steps = [
            self._git("fetch", self.remote, plan.base),
            self._git("checkout", "-B", plan.branch, f"{self.remote}/{plan.base}"),
        ]
        for r in steps:
            if r.returncode != 0:
                self.last_error = (r.stderr or r.stdout)[:300]
                return PublishResult(ok=False, branch=plan.branch, detail=self.last_error)

        target = self.clone_dir / plan.file_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.content, encoding="utf-8")
        except Exception as e:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=f"write {plan.file_path}: {e}")

        add = self._git("add", plan.file_path)
        if add.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(add.stderr or "git add failed")[:300])
        # Nothing staged => the artifact is byte-identical to base (goose rebuilt
        # an existing file). `git commit` would exit 1 with "nothing to commit" on
        # STDOUT (empty stderr -> the bare "git commit failed" fallback), which the
        # publisher loop treats as a hard failure and BREAKS on -- head-of-line
        # blocking every newer artifact behind a no-op forever. Detect it here and
        # report an idempotent no-op success instead. (`git diff --cached --quiet`
        # exits 0 when there are no staged changes, 1 when there are.)
        if self._git("diff", "--cached", "--quiet").returncode == 0:
            return PublishResult(ok=True, noop=True, branch=plan.branch,
                                 detail="no-op: artifact already on base (nothing to commit)")
        commit = self._git(
            "-c", f"user.name={self.author_name}",
            "-c", f"user.email={self.author_email}",
            "commit", "-m", plan.title,
        )
        if commit.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(commit.stderr or "git commit failed")[:300])

        push = self._run_with_backoff(
            lambda: self._git("push", "-u", self.remote, plan.branch, "--force-with-lease"))
        if push.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(push.stderr or "git push failed")[:300])

        # Create the PR WITHOUT --label: a missing label makes `gh pr create`
        # fail and open NO pr (observed: "could not add label: 'autonomous-build'
        # not found" -> every publish failed, watermark stuck). Labels are
        # cosmetic; attach them best-effort AFTER the PR exists so a label issue
        # can never block a PR.
        gh_args = ["gh", "pr", "create", "--repo", self.repo,
                   "--base", plan.base, "--head", plan.branch,
                   "--title", plan.title, "--body", plan.body]
        pr = self._run_with_backoff(
            lambda: subprocess.run(gh_args, capture_output=True, text=True,
                                   cwd=str(self.clone_dir)))
        if pr.returncode != 0:
            err = ((pr.stderr or "") + (pr.stdout or "")).lower()
            if "already exists" in err:
                # Idempotent: a PR for this branch already exists -- typically a
                # prior cycle opened it but its mesh state-write was dropped (e.g.
                # write_service timeout), so the publisher re-attempts. Treat as
                # SUCCESS (the PR is the desired end state) and recover its URL,
                # so the watermark can finally advance instead of stalling here.
                view = subprocess.run(
                    ["gh", "pr", "view", plan.branch, "--repo", self.repo,
                     "--json", "url", "-q", ".url"],
                    capture_output=True, text=True, cwd=str(self.clone_dir))
                pr_url = view.stdout.strip() if view.returncode == 0 else None
                if pr_url:
                    self._apply_labels_best_effort(pr_url, plan.labels)
                return PublishResult(ok=True, pr_url=pr_url, branch=plan.branch,
                                     detail="already exists")
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(pr.stderr or "gh pr create failed")[:300])
        pr_url = pr.stdout.strip()
        self._apply_labels_best_effort(pr_url, plan.labels)
        return PublishResult(ok=True, pr_url=pr_url, branch=plan.branch,
                             detail="published")

    def _apply_labels_best_effort(self, pr_url: str, labels: List[str]) -> None:
        """Attach labels to an already-open PR, creating any that don't exist.
        Every step is swallowed -- labels must never fail a published PR."""
        if not labels:
            return
        for lab in labels:
            # idempotent create (--force: create or no-op-update); ignore failure
            subprocess.run(["gh", "label", "create", lab, "--repo", self.repo, "--force"],
                           capture_output=True, text=True, cwd=str(self.clone_dir))
        add_args = ["gh", "pr", "edit", pr_url, "--repo", self.repo]
        for lab in labels:
            add_args += ["--add-label", lab]
        r = subprocess.run(add_args, capture_output=True, text=True, cwd=str(self.clone_dir))
        if r.returncode != 0:
            self.last_error = f"labels best-effort failed (PR still open): {(r.stderr or '')[:160]}"
