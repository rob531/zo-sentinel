"""
gitops.py -- the git/GitHub seam for the PR publisher.

Mirrors the ingestor's store seam: a Protocol with a hermetic Fake (tests /
dormant dry-run) and a real Cli implementation (a host clone + `git` + `gh`).
The publisher never touches the live working tree -- CliGitOps operates only
inside its own `clone_dir`, and auth is whatever `gh` already holds (an
AgentVault-hydrated token on the host), never a raw env secret.
"""
from __future__ import annotations

import os
import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol

from . import auto_declare


def _repo_relative(file_path: str) -> str:
    """Coerce an artifact path to repo-relative. Producers sometimes emit an
    ABSOLUTE path into the live tree (e.g. /home/workspace/zo_sentinel/foo.py).
    Joined onto the pub clone, an absolute right operand WINS
    (Path('/clone') / '/abs' == Path('/abs')), so the write escapes the clone and
    `git add /abs` fatals 'outside repository' -- which then head-of-line-blocks
    the whole queue. Strip the known source root; fall back to basename if it
    still points outside the repo."""
    if not os.path.isabs(file_path):
        return file_path
    src_root = os.environ.get("ZO_SENTINEL_ROOT", "/home/workspace/zo_sentinel")
    try:
        rel = os.path.relpath(file_path, src_root)
    except ValueError:
        rel = os.path.basename(file_path)
    if rel.startswith("..") or os.path.isabs(rel):
        rel = os.path.basename(file_path)
    return rel.replace("\\", "/")   # git wants forward slashes on every OS

# Characters that are RESERVED in Windows filenames. A path containing any of
# them is legal on ubuntu (where CI runs) and IMPOSSIBLE to materialise on the
# tower -- `git checkout` and `git worktree add` both fatal with "invalid path",
# so a single such file makes the WHOLE BRANCH un-checkout-able for every lane on
# the box. That is FU-209, measured 2026-07-31: one scaffold `__init__.py` under a
# literal `<service_name>/` directory broke step 0 of every Windows lane and the
# prod dry-run gate, with all seven required checks green and no way for them to
# be otherwise.
#
# The guard lives HERE, at the publisher's single commit chokepoint, rather than
# in CI, because CI cannot see this class by construction (ubuntu-latest accepts
# the name) and because HARNESS DOCTRINE forbids answering a finding with another
# required check. Rejecting at emit time is also the only point where the artifact
# can still be discarded cheaply.
_WINDOWS_RESERVED_CHARS = '<>:"|?*'


def _portable_path_violation(rel_path: str) -> Optional[str]:
    """Return a human-readable reason if `rel_path` cannot exist on Windows, else
    None. Drive-letter colons are already gone by this point: _repo_relative()
    runs first and coerces absolute paths, so any surviving ':' is genuinely
    inside a component name."""
    bad = sorted({c for c in rel_path if c in _WINDOWS_RESERVED_CHARS})
    if not bad:
        return None
    return ("path %r contains Windows-reserved character(s) %s; it would make "
            "every checkout of the branch fail on the tower (FU-209). Refusing "
            "to commit it. This is almost always an UNSUBSTITUTED template "
            "placeholder in an emitted path." % (rel_path, " ".join(bad)))


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


# Substrings git/gh emit on a TRANSIENT network failure -- a blip, not a real
# error. These must back off + retry (same as rate-limits) rather than break the
# whole publish cycle: a broken pipe on `git fetch` is recoverable, and treating
# it as a hard failure stalls the queue exactly like the no-op-commit bug did.
_TRANSIENT_NET_MARKERS = (
    "broken pipe",
    "send failure",
    "connection reset",
    "connection timed out",
    "could not resolve host",
    "failed to connect",
    "could not read from remote repository",
    "unexpected disconnect",
    "rpc failed",
    "ssl_read",
    "gnutls_handshake",
    "timed out",
    "temporary failure in name resolution",
)


def _is_transient_net(text: Optional[str]) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _TRANSIENT_NET_MARKERS)


def _is_retryable(text: Optional[str]) -> bool:
    return _is_rate_limited(text) or _is_transient_net(text)


_DIRTY_TREE_MARKERS = (
    "would be overwritten by checkout",
    "would be overwritten by merge",
    "Please commit your changes or stash them",
)


def _is_dirty_tree(text: Optional[str]) -> bool:
    """True when git refuses a checkout because the clone's working tree has
    uncommitted local modifications. Deterministic (NOT transient): without
    intervention every subsequent publish fails identically."""
    t = text or ""
    return any(m in t for m in _DIRTY_TREE_MARKERS)


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
    # True for a DETERMINISTIC failure that re-running cannot fix (bad path,
    # unwritable, malformed) -- as opposed to a transient network/rate-limit blip.
    # The publisher QUARANTINES a permanent failure (advances past it) instead of
    # breaking, so one poison artifact can't head-of-line-block every newer PR.
    permanent: bool = False


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
        """Run a network step; on a GitHub rate-limit OR transient-network signal
        (broken pipe, connection reset, DNS blip, ...), exponential backoff (with
        jitter) and retry up to max_retries. Other failures return immediately
        (the caller surfaces them as ok=False)."""
        attempt = 0
        while True:
            r = fn()
            if r.returncode == 0:
                return r
            err = (r.stderr or "") + (r.stdout or "")
            if attempt >= self.max_retries or not _is_retryable(err):
                return r
            delay = min(self.backoff_cap_sec,
                        self.backoff_base_sec * (2 ** attempt))
            delay += self._rng.uniform(0.0, delay * 0.25)   # jitter, no thundering herd
            self.last_error = (f"rate-limited; backoff {delay:.0f}s "
                               f"(attempt {attempt + 1}/{self.max_retries})")
            self._sleep(delay)
            attempt += 1

    def _stash_dirty_tree(self) -> bool:
        """Preserve illegitimate local edits in the pub clone as a stash entry
        (forensics-first), returning True when the tree is clean for a retry."""
        ts = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
        r = self._git("stash", "push", "--include-untracked",
                      "-m", f"publisher-selfheal {ts}")
        return r.returncode == 0

    def publish(self, plan: PublishPlan) -> PublishResult:
        # fetch + checkout go through backoff: a transient network blip (broken
        # pipe, connection reset, DNS) on these pre-steps is recoverable and must
        # not break the whole cycle -- it would otherwise stall the queue the way
        # the no-op-commit bug did, just on a flakier trigger.
        #
        # fetch ALL refs (+ --prune), not just `base`: the push below uses
        # --force-with-lease, whose lease compares the LOCAL origin/<branch>
        # tracking ref against the remote. After a container reboot the pub clone's
        # tracking refs go STALE, so re-touching a branch that already has an open
        # PR fails the lease -- "! [rejected] ... (stale info) / failed to push
        # some refs" -- which the publisher treats as transient -> break -> it
        # head-of-line-blocks every NEWER artifact (observed 2026-06-15 post-reboot:
        # real PRs stuck at #178 while builds kept producing artifacts). Fetching
        # all refs first makes origin/<branch> current so the lease is accurate: it
        # succeeds on a stale-but-unchanged branch and still safely REFUSES if a
        # human actually pushed to the branch (lease kept -- not downgraded to a
        # blind --force).
        steps = [
            lambda: self._git("fetch", "--prune", self.remote),
            lambda: self._git("checkout", "-B", plan.branch, f"{self.remote}/{plan.base}"),
        ]
        for idx, step in enumerate(steps):
            r = self._run_with_backoff(step)
            if (r.returncode != 0 and idx == 1
                    and _is_dirty_tree((r.stderr or "") + (r.stdout or ""))):
                # Dirty-tree self-heal (2026-07-15): an out-of-band edit inside
                # the pub clone (a stray working-tree edit deleted the saturated-
                # family gate on 2026-07-13) made EVERY checkout fail with
                # 'would be overwritten by checkout' and wedged publishing for
                # ~2 days. The pub clone is publisher-only -- any uncommitted
                # local change is illegitimate -- so preserve the evidence in a
                # stash (recoverable via `git stash list`, never a silent
                # discard) and retry the checkout ONCE.
                if self._stash_dirty_tree():
                    r = self._run_with_backoff(step)
            if r.returncode != 0:
                self.last_error = (r.stderr or r.stdout)[:300]
                return PublishResult(ok=False, branch=plan.branch, detail=self.last_error)

        # Coerce to repo-relative FIRST: an absolute path escapes the clone and
        # fatals git-add 'outside repository' (deterministic -> permanent).
        rel_path = _repo_relative(plan.file_path)

        # ...then refuse a path that cannot exist on Windows. permanent=True on
        # purpose: this artifact is malformed at its root and no retry fixes it,
        # so the queue retires it and keeps moving rather than stalling behind it.
        # Do NOT "helpfully" sanitise the name -- an unsubstituted placeholder
        # renamed to something legal would commit a wrongly-named service, which
        # is a quieter failure than the one being prevented.
        _violation = _portable_path_violation(rel_path)
        if _violation is not None:
            return PublishResult(ok=False, branch=plan.branch, permanent=True,
                                 detail=_violation[:300])

        target = self.clone_dir / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.content, encoding="utf-8")
        except Exception as e:
            return PublishResult(ok=False, branch=plan.branch, permanent=True,
                                 detail=f"write {rel_path}: {e}")

        add = self._git("add", rel_path)
        if add.returncode != 0:
            return PublishResult(ok=False, branch=plan.branch, permanent=True,
                                 detail=(add.stderr or "git add failed")[:300])

        # Declare-or-mount (CofC 2026-07-21). The reachability ratchet enforces
        # that a PR adding an unmounted router either mounts it or names it in
        # tools/reachability_deferred.json. The builder cannot do either -- the
        # module_from_exemplar lane guard forbids self-mounting and it emits
        # exactly one file -- so without this the gate would be unsatisfiable
        # for every autonomous build and ~15 PRs/day would go red for a rule
        # none could comply with. The declaration is written HERE, by ordinary
        # deterministic publisher code, so the lane guard is untouched and the
        # builder gains no write scope. A declared router is still counted as an
        # orphan; what this buys is that the growth is no longer SILENT.
        changed, detail = auto_declare.declare(
            self.clone_dir, rel_path, plan.content,
            task=getattr(plan, "dedup_key", None))
        if changed:
            d_add = self._git("add", auto_declare.DEFERRED_REL)
            if d_add.returncode != 0:
                # Non-fatal by design: losing the declaration means the ratchet
                # flags this PR, which is loud and correct. Losing the artifact
                # would not be.
                self.last_error = "auto-declare stage failed: %s" % (
                    (d_add.stderr or "")[:200])

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
