"""A wave must record which tree fired it.

`HERE = Path(__file__).resolve().parent` has been computed at import since the
first version of `weekly_rescore.py` and has never once been written down. So no
artifact a wave leaves behind -- not `state.json`, not the ledger, not the report
-- says which worktree produced it.

The cost is measurable, not hypothetical. "Which worktree does
`moat-rescore-weekly` actually RUN from?" has been open since 2026-08-11 and was
still open on 2026-09-13, when a census of **94 rescore trees** on the tower
could only narrow it to 33 by eliminating the trees that lacked a symbol the
09-09 wave demonstrably used. Thirty-three candidates, for a question the
process could have answered about itself for free at fire time.

R1 tells every lane to resolve the running artifact from the RUNTIME rather than
from a repo path. This is the runtime writing its own answer down, once, at the
moment it matters -- the same shape as #4814 (a status the watch loop already
held and omitted from the line a human reads) and FU-358.

Report-only. No gate, no new required check, no exit-code change (R7).

Every assertion below carries BOTH poles. The load-bearing ones:
  * a tree that is NOT a git checkout must read UNKNOWN, never a fabricated sha
  * a raising/timing-out git must not propagate into a paid fire
  * `driver_seen` must gain a SECOND entry when the tree changes -- recording only
    the first observation would hide the one case the field exists to expose
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "weekly_rescore.py"


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weekly_rescore"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _fresh_cache(wr):
    """The helper caches per process; every test must observe a fresh computation."""
    wr._DRIVER_PROV = None
    yield
    wr._DRIVER_PROV = None


@pytest.fixture
def run(wr, tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "ledger", lambda *a, **k: None)
    r = wr.Run(tmp_path / "20260913-000000")
    r.state["run_id"] = "20260913-000000"
    return r


# --------------------------------------------------------------- the helper

def test_provenance_names_every_field(wr):
    prov = wr._driver_provenance()
    assert set(prov) == {"launch_dir", "git_sha", "dirty", "driver_md5", "host"}


def test_launch_dir_is_the_tree_the_module_came_from_not_the_cwd(wr, tmp_path, monkeypatch):
    """The bug this fixes is 'which TREE', and cwd is a different question."""
    monkeypatch.chdir(tmp_path)
    prov = wr._driver_provenance()
    assert prov["launch_dir"] == str(wr.HERE)
    assert prov["launch_dir"] != str(tmp_path)


def test_in_a_real_checkout_the_sha_is_a_real_sha(wr):
    """Pole 1 of 2. Without this, UNKNOWN-everywhere would pass the suite."""
    prov = wr._driver_provenance()
    assert prov["git_sha"] != "UNKNOWN"
    assert len(prov["git_sha"]) == 40
    assert all(c in "0123456789abcdef" for c in prov["git_sha"])
    assert isinstance(prov["dirty"], bool)


def test_outside_a_git_checkout_the_sha_is_UNKNOWN_never_invented(wr, monkeypatch):
    """Pole 2 of 2, and the one that matters: git's real failure contract.

    `git rev-parse HEAD` outside a work tree exits 128. A helper that answered
    with a plausible default here would put a WRONG tree in the record, which is
    worse than a blank one: unknown is not zero (R6).
    """
    real = subprocess.run

    def fake(argv, *a, **k):
        if argv and argv[0] == "git":
            return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")
        return real(argv, *a, **k)

    monkeypatch.setattr(wr.subprocess, "run", fake)
    prov = wr._driver_provenance()
    assert prov["git_sha"] == "UNKNOWN"
    assert prov["dirty"] == "UNKNOWN"
    # degradation is per-field: what CAN be known still is
    assert prov["launch_dir"] == str(wr.HERE)
    assert prov["driver_md5"] != "UNKNOWN"


def test_a_raising_git_never_propagates_into_a_paid_fire(wr, monkeypatch):
    """A provenance helper that can kill a fire is worse than no helper."""
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(wr.subprocess, "run", boom)
    prov = wr._driver_provenance()          # must not raise
    assert prov["git_sha"] == "UNKNOWN"
    assert prov["dirty"] == "UNKNOWN"


def test_driver_md5_is_this_file_and_the_assertion_is_not_vacuous(wr):
    import hashlib
    expected = hashlib.md5(MODULE_PATH.read_bytes()).hexdigest()
    other = hashlib.md5(Path(__file__).resolve().read_bytes()).hexdigest()
    prov = wr._driver_provenance()
    assert prov["driver_md5"] == expected
    assert prov["driver_md5"] != other      # a constant would satisfy the first line


def test_the_cache_is_per_process_not_per_call(wr, monkeypatch):
    first = wr._driver_provenance()
    calls = []
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError))
    second = wr._driver_provenance()
    assert second == first
    assert calls == []                      # cached: git was not re-invoked


def test_a_mutated_return_cannot_poison_the_cache(wr):
    """Callers get a copy; one caller's edit must not become every caller's truth."""
    a = wr._driver_provenance()
    a["git_sha"] = "tampered"
    assert wr._driver_provenance()["git_sha"] != "tampered"


# ------------------------------------------------------------ Run.mark wiring

def test_mark_writes_driver_into_state_and_it_survives_reload(wr, run):
    run.mark("fire")
    reloaded = wr.Run(run.dir)
    assert reloaded.state["driver"]["launch_dir"] == str(wr.HERE)
    assert reloaded.state["driver_seen"] == [[str(wr.HERE), wr._driver_provenance()["git_sha"]]]


def test_driver_seen_dedups_within_one_tree(wr, run):
    for phase in ("export", "fire", "watch", "import", "postcheck"):
        run.mark(phase)
    assert len(run.state["driver_seen"]) == 1


def test_driver_seen_RECORDS_a_second_tree(wr, run, tmp_path, monkeypatch):
    """The case the field exists for: fired from one tree, collected from another.

    Recording only the first observation would make this invisible, which is the
    same defect one level up -- a value held and never published.
    """
    run.mark("fire")
    monkeypatch.setattr(wr, "HERE", tmp_path / "some-other-worktree" / "tools" / "rescore")
    wr._DRIVER_PROV = None
    run.mark("collect")
    assert len(run.state["driver_seen"]) == 2
    assert run.state["driver"]["launch_dir"] == str(wr.HERE)      # most recent wins


def test_mark_still_does_its_job_when_provenance_is_broken(wr, run, monkeypatch):
    """Provenance is an addition. It must never be able to break the phase record."""
    def boom():
        raise RuntimeError("provenance exploded")

    monkeypatch.setattr(wr, "_driver_provenance", boom)
    run.mark("fire", instance_id=12345)
    assert run.state["phases"]["fire"] == "done"
    assert run.state["instance_id"] == 12345


def test_mark_preserves_its_existing_contract(wr, run):
    run.mark("export", "failed", exported=99)
    assert run.state["phases"]["export"] == "failed"
    assert run.state["exported"] == 99
    assert run.done("export") is False
    run.mark("export", exported=99)
    assert run.done("export") is True
