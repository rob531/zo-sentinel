"""
test_producer_commit.py -- RCA cross-cycle-2026-10 Fix 3: the producer versions
what it writes; the watermark publisher path is retired.

TWO-POLE (the proof standard, not a new gate):
  RED   restore the write-to-disk-only producer -> the runtime tree carries files
        origin/main does not (the CI scar's 462, in miniature).
  GREEN the producer commits through ProducerCommit -> every written file is
        either on origin/main after the gated merge, or evicted from the tree;
        the trees converge (staged_repo_reconcile census == 0).

Everything runs on real throwaway git repos (an origin, a runtime checkout) with
a GitOps double that "merges on green" into origin -- no network, no host.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zo_sentinel.publisher.gitops import FakeGitOps, PublishResult
from zo_sentinel.publisher.publisher import ProducerCommit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import staged_repo_reconcile as srr  # noqa: E402

GOOD_API = ("from fastapi import APIRouter\nfrom app.db import get_session\n"
            "router = APIRouter()\n@router.get('/y')\ndef y():\n    return 1\n")
HOLLOW_API = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/x')\ndef x():\n    return {}\n"


def _git(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    assert p.returncode == 0, (a, p.stderr)
    return p.stdout


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    home = tmp_path / "runtime"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    for c in (seed,):
        _git(c, "config", "user.email", "t@t")
        _git(c, "config", "user.name", "t")
    (seed / "services" / "active" / "base").mkdir(parents=True)
    (seed / "services" / "active" / "base" / "__init__.py").write_text("# base\n")
    (seed / "existing.py").write_text("SHIPPED = 1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(home))
    (home / "directives").mkdir()
    return {"tmp": tmp_path, "origin": origin, "home": home}


class MergeOnGreen:
    """A GitOps double: the PR passes its gates and merges into origin/main."""

    def __init__(self, tmp: Path, origin: Path, fail=None):
        self.work = tmp / "pub_clone"
        _git(tmp, "clone", "-q", str(origin), str(self.work))
        _git(self.work, "config", "user.email", "p@p")
        _git(self.work, "config", "user.name", "p")
        self.published = []
        self.fail = dict(fail or {})   # file -> PublishResult to return once

    def publish(self, plan):
        if plan.file_path in self.fail:
            return self.fail.pop(plan.file_path)
        _git(self.work, "pull", "-q", "--ff-only", "origin", "main")
        dest = self.work / plan.file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(plan.content)
        _git(self.work, "add", "--", plan.file_path)
        if not _git(self.work, "diff", "--cached", "--name-only").strip():
            return PublishResult(ok=True, noop=True, detail="no-op")
        _git(self.work, "commit", "-q", "-m", plan.title)
        _git(self.work, "push", "-q", "origin", "main")
        self.published.append(plan)
        return PublishResult(ok=True, pr_url="https://example/pull/%d" % len(self.published))


def _pc(world, gitops, **kw):
    kw.setdefault("enabled_override", True)
    kw.setdefault("pr_spacing_sec", 0)
    return ProducerCommit(gitops=gitops, home=str(world["home"]),
                          outbox=str(world["tmp"] / "state" / "outbox.json"),
                          quarantine_dir=str(world["tmp"] / "quarantine"),
                          sleep=lambda s: None, **kw)


def _builder_writes(home: Path):
    """What goose writes in one pass: two good builds and two that the pre-PR
    rules refuse (a hollow scaffold, a saturated-family permutation)."""
    files = {
        "real_api.py": GOOD_API,
        "services/active/new_svc/router.py": "ROUTES = []\n",
        "hollow_api.py": HOLLOW_API,
        "fleet_risk_permutation_api.py": "x = 1\n",
    }
    for rel, src in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return list(files)


def _drift(home: Path) -> list:
    """CODE in the runtime tree vs its HEAD (== origin/main after the deploy ff).
    directives/ holds runtime sentinels (.done/.failed state), not code."""
    out = _git(home, "status", "--porcelain", "--untracked-files=all")
    return [line for line in out.splitlines()
            if line.strip() and not line[3:].startswith("directives/")]


def _deploy(home: Path):
    _git(home, "pull", "-q", "--ff-only", "origin", "main")


# ---------------------------------------------------------------- two-pole

def test_red_pole_write_to_disk_only_producer_diverges(world):
    home = world["home"]
    _builder_writes(home)                 # the old producer: write, no git
    _deploy(home)
    assert len(_drift(home)) == 4
    census = srr.measure(str(home), "origin/main", ["services/active"], [".py"])
    assert census["missing_from_ref"] == 1, census


def test_green_pole_producer_commits_and_trees_converge(world):
    home = world["home"]
    gitops = MergeOnGreen(world["tmp"], world["origin"])
    pc = _pc(world, gitops)
    results = {rel: pc.commit(rel, task="build_" + Path(rel).stem)
               for rel in _builder_writes(home)}
    assert results["real_api.py"]["action"] == "published"
    assert results["services/active/new_svc/router.py"]["action"] == "published"
    assert results["hollow_api.py"]["action"] == "hollow_blocked"
    assert results["fleet_risk_permutation_api.py"]["action"] == "saturated_family"
    _deploy(home)                         # deploy-runtime-from-main ff
    assert _drift(home) == [], _drift(home)
    census = srr.measure(str(home), "origin/main", ["services/active"], [".py"])
    assert census["missing_from_ref"] == 0, census
    # refused bytes are KEPT for inspection, outside the tree
    kept = list((world["tmp"] / "quarantine" / "evicted").rglob("hollow_api.py"))
    assert kept and kept[0].read_text() == HOLLOW_API


# ---------------------------------------------------------------- semantics

def test_dormant_enqueues_and_publishes_nothing_then_drains(world):
    home = world["home"]
    (home / "a.py").write_text("print('a')\n")
    g = FakeGitOps()
    pc = _pc(world, g, enabled_override=False)
    assert pc.commit("a.py")["action"] == "dormant"
    assert g.published == [] and pc.status()["counts"] == {"pending": 1}
    pc2 = _pc(world, g)                    # a NEW instance: the outbox is durable
    assert [r["action"] for r in pc2.drain()] == ["published"]
    assert pc2.status()["counts"] == {"published": 1}


def test_no_clone_never_marks_published(world):
    (world["home"] / "a.py").write_text("print('a')\n")
    pc = _pc(world, None)
    assert pc.commit("a.py")["action"] == "no_clone"
    assert pc.status()["counts"] == {"pending": 1}


def test_transient_failure_stays_pending_and_is_retried(world):
    (world["home"] / "a.py").write_text("print('a')\n")
    g = MergeOnGreen(world["tmp"], world["origin"],
                     fail={"a.py": PublishResult(ok=False, detail="HTTP 502")})
    pc = _pc(world, g)
    assert pc.commit("a.py")["action"] == "transient"
    assert pc.status()["counts"] == {"pending": 1}
    assert [r["action"] for r in pc.drain()] == ["published"]


def test_permanent_failure_is_quarantined_and_evicted(world):
    home = world["home"]
    (home / "a.py").write_text("print('a')\n")
    g = MergeOnGreen(world["tmp"], world["origin"],
                     fail={"a.py": PublishResult(ok=False, permanent=True, detail="bad path")})
    r = _pc(world, g).commit("a.py")
    assert r["action"] == "quarantined" and r["evicted"]
    assert not (home / "a.py").exists()


def test_refused_rebuild_of_a_tracked_file_restores_head(world):
    home = world["home"]
    (home / "existing.py").write_text('SQL = "DROP TABLE mcp_risk_register"\n')
    r = _pc(world, FakeGitOps()).commit("existing.py")
    assert r["action"] == "blocked" and r["evicted"]
    assert (home / "existing.py").read_text() == "SHIPPED = 1\n"


def test_duplicate_module_within_window_is_refused(world):
    home = world["home"]
    g = MergeOnGreen(world["tmp"], world["origin"])
    pc = _pc(world, g)
    (home / "dup_api.py").write_text(GOOD_API)
    assert pc.commit("dup_api.py", task="t1")["action"] == "published"
    _deploy(home)
    (home / "dup_api.py").write_text(GOOD_API + "# rebuilt by another directive\n")
    r = pc.commit("dup_api.py", task="t2")
    assert r["action"] == "duplicate_module"
    assert (home / "dup_api.py").read_text() == GOOD_API     # restored to HEAD


def test_noop_does_not_burn_cap_and_cap_defers_without_dropping(world):
    home = world["home"]
    g = MergeOnGreen(world["tmp"], world["origin"])
    pc = _pc(world, g, daily_cap=1)
    (home / "existing.py").write_text("SHIPPED = 1\n")      # byte-identical -> noop
    assert pc.commit("existing.py")["action"] == "noop"
    (home / "b.py").write_text("print('b')\n")
    (home / "c.py").write_text("print('c')\n")
    assert pc.commit("b.py")["action"] == "published"
    assert pc.commit("c.py")["action"] == "deferred_cap"
    assert pc.status()["counts"].get("pending") == 1        # deferred, never dropped


def test_hollow_build_parks_its_directive(world):
    from zo_sentinel.build_completion import failed_quarantined
    home = world["home"]
    (home / "directives" / "build_x.done.json").write_text("{}")
    (home / "hollow_api.py").write_text(HOLLOW_API)
    r = _pc(world, FakeGitOps()).commit("hollow_api.py", task="build_x")
    assert r["action"] == "hollow_blocked" and r["parked"] is True
    assert failed_quarantined("build_x", home / "directives", world["tmp"] / "quarantine")
    assert not (home / "directives" / "build_x.done.json").exists()


def test_hollow_build_without_a_task_parks_nothing(world):
    (world["home"] / "hollow_api.py").write_text(HOLLOW_API)
    r = _pc(world, FakeGitOps()).commit("hollow_api.py", task="")
    assert r["action"] == "hollow_blocked" and r["parked"] is False


def test_non_root_and_non_py_skip_hollow_gate(world):
    home = world["home"]
    (home / "app").mkdir()
    (home / "app" / "sub_module.py").write_text("app = FastAPI()\n")
    (home / "notes.md").write_text("app = FastAPI()\n")
    pc = _pc(world, FakeGitOps())
    assert pc.commit("app/sub_module.py")["action"] == "published"
    assert pc.commit("notes.md")["action"] == "published"


def test_backfill_publishes_good_and_HOLDS_refused_until_ruled(world):
    home = world["home"]
    _builder_writes(home)                 # the historical host-only backlog
    pc = _pc(world, MergeOnGreen(world["tmp"], world["origin"]))
    missing = ["real_api.py", "hollow_api.py"]
    acts = {r["file"]: r["action"] for r in pc.backfill(missing)}
    assert acts == {"real_api.py": "published", "hollow_api.py": "held"}
    assert (home / "hollow_api.py").exists()         # not deleted without a ruling
    ev = pc.evict_held()
    assert ev and ev[0]["evicted"] and not (home / "hollow_api.py").exists()


def test_lost_file_is_terminal_and_loud(world):
    pc = _pc(world, FakeGitOps())
    assert pc.commit("never_written.py")["action"] == "lost"
    assert pc.status()["counts"] == {"lost": 1}


def test_outbox_is_plain_json_outside_the_tree(world):
    (world["home"] / "a.py").write_text("print('a')\n")
    pc = _pc(world, FakeGitOps())
    pc.commit("a.py")
    d = json.loads(Path(pc.outbox).read_text())
    assert d["entries"]["a.py"]["status"] == "published"
    assert not str(pc.outbox).startswith(str(world["home"]))


def test_duplicate_window_expires(world):
    home = world["home"]
    clock = {"now": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    pc = _pc(world, MergeOnGreen(world["tmp"], world["origin"]),
             clock=lambda: clock["now"])
    (home / "w_api.py").write_text(GOOD_API)
    assert pc.commit("w_api.py", built_at="2026-10-01T00:00:00Z")["action"] == "published"
    _deploy(home)
    clock["now"] += timedelta(days=5)
    (home / "w_api.py").write_text(GOOD_API + "# v2\n")
    assert pc.commit("w_api.py", built_at="2026-10-06T00:00:00Z")["action"] == "published"
