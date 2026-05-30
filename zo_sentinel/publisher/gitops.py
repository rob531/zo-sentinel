"""
gitops.py -- the git/GitHub seam for the PR publisher.

Mirrors the ingestor's store seam: a Protocol with a hermetic Fake (tests /
dormant dry-run) and a real Cli implementation (a host clone + `git` + `gh`).
The publisher never touches the live working tree -- CliGitOps operates only
inside its own `clone_dir`, and auth is whatever `gh` already holds (an
AgentVault-hydrated token on the host), never a raw env secret.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol


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
                 author_email: str = "bot@zocomputer.io"):
        self.clone_dir = Path(clone_dir)
        self.repo = repo
        self.remote = remote
        self.author_name = author_name
        self.author_email = author_email
        self.last_error: Optional[str] = None

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.clone_dir), *args],
            capture_output=True, text=True,
        )

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
        commit = self._git(
            "-c", f"user.name={self.author_name}",
            "-c", f"user.email={self.author_email}",
            "commit", "-m", plan.title,
        )
        if commit.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(commit.stderr or "git commit failed")[:300])

        push = self._git("push", "-u", self.remote, plan.branch, "--force-with-lease")
        if push.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(push.stderr or "git push failed")[:300])

        gh_args = ["gh", "pr", "create", "--repo", self.repo,
                   "--base", plan.base, "--head", plan.branch,
                   "--title", plan.title, "--body", plan.body]
        for lab in plan.labels:
            gh_args += ["--label", lab]
        pr = subprocess.run(gh_args, capture_output=True, text=True,
                            cwd=str(self.clone_dir))
        if pr.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch,
                                 detail=(pr.stderr or "gh pr create failed")[:300])
        return PublishResult(ok=True, pr_url=pr.stdout.strip(), branch=plan.branch,
                             detail="published")
