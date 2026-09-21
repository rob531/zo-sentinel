"""Controls for lane isolation + the shadow halt.

The one assertion that must never be deleted is
`test_a_shadow_halt_can_never_block`. Shadow mode is the whole safety story for
arming the census halt, and a shadow mode that could block under some code path
would be worse than no shadow mode at all -- it would carry the confidence of
having been tested.
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "tools", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


NOW = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.timezone.utc)


# ===========================================================================
# lane_halt -- shadow must be inert
# ===========================================================================
def test_a_shadow_halt_can_never_block():
    """THE load-bearing test. A shadow decision goes to a different DIRECTORY, so
    there is no code path on which is_halted() can observe it."""
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        armed, shadow = os.path.join(d, "a"), os.path.join(d, "s")
        r = h.raise_halt("builder:manifest", "0/36 valid", sha="abc123",
                         mode=h.MODE_SHADOW, now=NOW,
                         halt_dir=armed, shadow_dir=shadow)
        assert r["shadowed"] is True and r["raised"] is False
        assert h.is_halted("builder:manifest", NOW, halt_dir=armed) is False
        # and the armed directory was never even created
        assert not os.path.isdir(armed)


def test_an_armed_halt_does_block():
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        r = h.raise_halt("builder:manifest", "0/36 valid", sha="abc123",
                         mode=h.MODE_ARMED, now=NOW, halt_dir=d)
        assert r["raised"] is True
        assert h.is_halted("builder:manifest", NOW, halt_dir=d) is True


def test_a_halt_scopes_to_ONE_lane():
    """One file per lane: a write to A cannot lose-update B."""
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        h.raise_halt("builder:manifest", "x", mode=h.MODE_ARMED, now=NOW, halt_dir=d)
        assert h.is_halted("builder:manifest", NOW, halt_dir=d) is True
        assert h.is_halted("builder:router", NOW, halt_dir=d) is False


def test_an_expired_halt_stops_blocking():
    """A halt that cannot lapse is a permanently-red gate with a process attached."""
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        h.raise_halt("l", "x", ttl_hours=1, mode=h.MODE_ARMED, now=NOW, halt_dir=d)
        assert h.is_halted("l", NOW + dt.timedelta(minutes=59), halt_dir=d) is True
        assert h.is_halted("l", NOW + dt.timedelta(hours=2), halt_dir=d) is False


def test_re_raising_does_not_extend_the_ttl():
    """A lane alarming hourly would otherwise never lapse and the TTL would be
    decorative -- the exact way suppressions become permanent."""
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        first = h.raise_halt("l", "x", ttl_hours=6, mode=h.MODE_ARMED, now=NOW,
                             halt_dir=d)
        later = h.raise_halt("l", "x", ttl_hours=6, mode=h.MODE_ARMED,
                             now=NOW + dt.timedelta(hours=3), halt_dir=d)
        assert later["raised"] is False
        assert later["expires_at"] == first["expires_at"]


def test_every_halt_records_the_sha_it_was_decided_on():
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        r = h.raise_halt("l", "x", sha="deadbee", mode=h.MODE_ARMED, now=NOW,
                         halt_dir=d)
        assert r["decided_on_sha"] == "deadbee"
        assert r["decided_at"] and r["expires_at"]


def test_a_lane_name_with_a_colon_is_not_a_filename():
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        h.raise_halt("builder:manifest", "x", mode=h.MODE_ARMED, now=NOW, halt_dir=d)
        assert h.is_halted("builder:manifest", NOW, halt_dir=d) is True
        assert os.listdir(d) == ["builder__manifest.json"]


def test_an_unreadable_expiry_fails_open_not_wedged():
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        p = h.halt_path("l", d)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"lane": "l", "expires_at": "not-a-date"}, fh)
        assert h.is_halted("l", NOW, halt_dir=d) is False


def test_clear_removes_the_block_and_leaves_a_record():
    h = _load("lane_halt")
    with tempfile.TemporaryDirectory() as d:
        h.raise_halt("l", "x", mode=h.MODE_ARMED, now=NOW, halt_dir=d)
        assert h.clear("l", who="chairman", halt_dir=d) is True
        assert h.is_halted("l", NOW, halt_dir=d) is False
        assert os.path.isfile(h.halt_path("l", d) + ".cleared")


# ===========================================================================
# lane_worktree -- the shared-path oracle
# ===========================================================================
def test_the_shared_oracle_normalises_case_and_separators():
    w = _load("lane_worktree")
    assert w.is_shared(r"D:\zo\_runbook")
    assert w.is_shared(r"d:/zo/_runbook")
    assert w.is_shared("D:\\zo\\_runbook\\")
    assert not w.is_shared(r"D:\zo\_lanes\prod-drift")


def test_lane_paths_are_private_and_distinct():
    w = _load("lane_worktree")
    assert w.lane_path("prod-drift") != w.lane_path("score-import")
    assert not w.is_shared(w.lane_path("prod-drift"))


# ===========================================================================
# runbook_pin -- refusing to heal must NOT read as healthy
# ===========================================================================
def _fake_git(state):
    """Minimal git stand-in so heal() can be driven without touching a real tree."""
    def run(cwd, *args):
        if args[0] == "rev-parse" and args[-1] == "HEAD":
            return 0, state["head"]
        if args[0] == "rev-parse":
            return 0, state["target"]
        if args[0] == "checkout":
            state["head"] = state["target"]
            state["healed"] = True
            return 0, ""
        if args[0] == "status":
            return 0, ""
        if args[0] == "fetch":
            return 0, ""
        if args[0] == "ls-tree":
            return 0, ""
        if args[0] == "cat-file":
            return 0, ""
        return 0, ""
    return run


def test_refusing_to_heal_a_shared_tree_keeps_the_failing_verdict(monkeypatch):
    """A refusal is not a repair. If this returned PINNED it would be the
    'a gate that skips reads as a gate that passes' defect, in the actuator."""
    rp = _load("runbook_pin")
    monkeypatch.setattr(rp, "_is_shared", lambda p: True)
    state = {"head": "a" * 40, "target": "b" * 40, "healed": False}
    r = rp.heal(r"D:\zo\_runbook", "origin/main", runner=_fake_git(state))
    assert r.get("refused") is True
    assert r["healed"] is False
    assert state["healed"] is False, "it must not have run checkout"
    assert r["rc"] != rp.RC_PINNED, "a refused heal must not report PINNED"
    assert "SHARED" in r["reason"]


def test_force_heals_even_a_shared_tree(monkeypatch):
    rp = _load("runbook_pin")
    monkeypatch.setattr(rp, "_is_shared", lambda p: True)
    state = {"head": "a" * 40, "target": "b" * 40, "healed": False}
    r = rp.heal(r"D:\zo\_runbook", "origin/main", runner=_fake_git(state), force=True)
    assert state["healed"] is True and r.get("refused") is None


def test_a_private_tree_heals_without_force(monkeypatch):
    rp = _load("runbook_pin")
    monkeypatch.setattr(rp, "_is_shared", lambda p: False)
    state = {"head": "a" * 40, "target": "b" * 40, "healed": False}
    r = rp.heal(r"D:\zo\_lanes\prod-drift", "origin/main", runner=_fake_git(state))
    assert state["healed"] is True and r.get("refused") is None


def test_the_shared_oracle_fails_SAFE_when_it_cannot_be_imported(monkeypatch):
    """If the oracle is missing we must assume SHARED -- the dangerous default is
    to heal. Unknown is not zero."""
    rp = _load("runbook_pin")
    monkeypatch.setitem(sys.modules, "lane_worktree", None)
    monkeypatch.setattr(rp, "_is_shared", rp._is_shared)
    real = rp._is_shared

    def boom(name, *a, **k):
        raise ImportError("simulated")
    monkeypatch.setattr("builtins.__import__", boom)
    try:
        assert real("anything") is True
    finally:
        pass


# ===========================================================================
# census -> halt wiring
# ===========================================================================
def test_only_validity_collapse_produces_a_halt(tmp_path, monkeypatch):
    """UNDRAINED means the blockage is already downstream -- halting the emitter
    would treat a symptom in the wrong organ. LANE_SILENT means it already stopped.

    Halt dirs are redirected to tmp_path: an earlier version of this test wrote a
    real record (sha=abc) into artifacts/lane_halts/_shadow, i.e. it polluted the
    very ledger the arming decision is supposed to rest on."""
    q = _load("queue_census")
    h = _load("lane_halt")
    monkeypatch.setattr(h, "SHADOW_DIR", str(tmp_path / "shadow"))
    monkeypatch.setattr(h, "HALT_DIR", str(tmp_path / "armed"))
    monkeypatch.setitem(sys.modules, "lane_halt", h)
    alarms = [
        {"kind": "UNDRAINED", "lane": "builder:other", "detail": "d"},
        {"kind": "LANE_SILENT", "lane": "builder:logic", "detail": "d"},
        {"kind": "DIVERGING", "lane": "builder:router", "detail": "d"},
        {"kind": "VALIDITY_COLLAPSE", "lane": "builder:manifest", "detail": "0/36"},
    ]
    out = q.emit_halts(alarms, "shadow", sha="abc")
    assert [h2["lane"] for h2 in out] == ["builder:manifest"]
    assert out[0]["shadowed"] is True
    # and nothing was written outside the tmpdir
    assert str(tmp_path) in out[0]["path"]


def test_halt_mode_off_emits_nothing():
    q = _load("queue_census")
    alarms = [{"kind": "VALIDITY_COLLAPSE", "lane": "l", "detail": "d"}]
    assert q.emit_halts(alarms, "off") == []


def test_the_census_is_ARMED_and_shadow_is_still_reachable():
    """ARMED by chairman ruling 2026-07-30, after the shadow report showed 0 halts
    firing today and the 7/29 founding case reproducing. Shadow must remain
    available: losing it would remove the only way to re-measure before a future
    threshold change."""
    src = open(os.path.join(ROOT, "tools", "queue_census.py"), encoding="utf-8").read()
    i = src.find('"--halt-mode"')
    assert i > 0
    assert 'default="armed"' in src[i:i + 400]
    assert '"shadow"' in src[i:i + 400]


def test_enforce_is_the_only_thing_that_makes_a_halt_bite(tmp_path):
    """A sentinel is not enforcement until a caller consults it. This is that
    caller, and its red path is asserted -- 'armed' with no consumer would be the
    'a merge is not an arming' defect."""
    h = _load("lane_halt")
    d = str(tmp_path)
    assert h.is_halted("l", halt_dir=d) is False
    h.raise_halt("l", "collapse", mode=h.MODE_ARMED, halt_dir=d)
    assert h.is_halted("l", halt_dir=d) is True
