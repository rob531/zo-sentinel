"""Regression for #5046: a TIME-ROTATED daemon was reported as drift.

`intent_engine_daemon` is run 06:00-21:59 ET by `intent_rotation_service.py`,
which runs `he_who_comes_next.py` the other eight hours. At 02:08 UTC (22:08 ET)
the rotator correctly swapped it out; the drift check called that MISSING, hit
18 consecutive cycles and filed chairman issue #5046 against a daemon that was
behaving exactly as designed.

NEGATIVE CONTROLS ARE THE POINT OF THIS FILE. The cure must not become a way for
any supervisor to vouch for any dead child -- that is the false green the whole
module exists to stop. So both poles are exercised on every surface:

    POSITIVE   rotator alive + sibling alive      -> rotated out, NOT missing
    NEGATIVE 1 rotator alive + NO sibling alive   -> still MISSING
    NEGATIVE 2 single-child wrapper alive         -> still MISSING (the #4706 class)
    NEGATIVE 3 rotator itself dead                -> still MISSING
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "tools" / "registration_drift_check.py"
spec = importlib.util.spec_from_file_location("registration_drift_check", SRC)
rdc = importlib.util.module_from_spec(spec)
sys.modules["registration_drift_check"] = rdc
spec.loader.exec_module(rdc)


@pytest.fixture()
def bed(tmp_path):
    """The #5046 shape on disk: a rotator naming two mutually-exclusive children."""
    intent = tmp_path / "intent_engine_daemon.py"
    hewho = tmp_path / "he_who_comes_next.py"
    intent.write_text("# the day child\n")
    hewho.write_text("# the night child\n")

    rotator = tmp_path / "intent_rotation_service.py"
    rotator.write_text(
        "INTENT_CMD = ['python', '%s']\n"
        "HEWHO_CMD = ['python', '%s']\n"
        "DAY_START = 6\nNIGHT_START = 22\n" % (intent, hewho))
    return {"intent": str(intent), "hewho": str(hewho), "rotator": str(rotator)}


def _proc(pid, script):
    return {"pid": str(pid), "script": str(script), "argv": "python %s" % script}


# --- the resolver, in isolation -------------------------------------------

def test_rotated_out_is_not_missing(bed):
    """POSITIVE POLE -- the exact 02:08 UTC state that filed #5046."""
    live = [_proc(24973, bed["rotator"]), _proc(41216, bed["hewho"])]
    owner = rdc._rotator_owns(bed["intent"], live)
    assert owner is not None
    assert owner["rotator_pid"] == "24973"
    assert owner["active_sibling"] == str(Path(bed["hewho"]).resolve())
    assert "he_who_comes_next.py" in owner["why"]


def test_rotator_alive_but_no_sibling_running_is_still_missing(bed):
    """NEGATIVE CONTROL 1 -- a BROKEN rotation must still report drift.

    If the rotator is up but neither child is, that is a real outage. A cure
    that swallowed this case would have converted #5046 from a false positive
    into a permanent blind spot, which is strictly worse.
    """
    live = [_proc(24973, bed["rotator"])]
    assert rdc._rotator_owns(bed["intent"], live) is None


def test_single_child_wrapper_can_never_vouch(tmp_path):
    """NEGATIVE CONTROL 2 -- the false green the module was built against.

    daemon_wrapper.sh carries its child's path as an argument. A live wrapper
    over a dead child is drift, not rotation: it names ONE command, so it can
    never satisfy the live-sibling clause.
    """
    child = tmp_path / "proposed_to_pending_promoter.py"
    child.write_text("# the only child\n")
    wrapper = tmp_path / "daemon_wrapper.sh"
    wrapper.write_text("exec python3 '%s'\n" % child)

    live = [_proc(100, wrapper)]
    assert rdc._rotator_owns(str(child), live) is None


def test_a_dead_rotator_cannot_excuse_its_child(bed):
    """NEGATIVE CONTROL 3 -- the rotator dying takes the excuse with it."""
    live = [_proc(41216, bed["hewho"])]          # sibling alive, rotator gone
    assert rdc._rotator_owns(bed["intent"], live) is None


def test_two_names_for_one_command_is_not_a_rotation(tmp_path):
    """A supervisor naming only ONE DISTINCT command is a wrapper, not a rotator."""
    only = tmp_path / "solo.py"
    only.write_text("# solo\n")
    rot = tmp_path / "rot.py"
    rot.write_text("A = ['python', '%s']\nB = ['python', '%s']\n" % (only, only))
    live = [_proc(1, rot)]
    assert rdc._rotator_owns(str(only), live) is None


# --- a whole cycle, both poles --------------------------------------------

def _isolate(monkeypatch, tmp_path, declared, live):
    """Run a whole cycle against a fake host, touching no real state or network."""
    monkeypatch.setattr(rdc, "GO_SH", tmp_path / "nonexistent_go.sh")
    monkeypatch.setattr(rdc, "WATCHDOG_SH", tmp_path / "nonexistent_watchdog.sh")
    monkeypatch.setattr(rdc, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(rdc, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(rdc, "DRIFT_LOG", tmp_path / "drift.log")
    monkeypatch.setattr(rdc, "merge_declared", lambda a, b: declared)
    monkeypatch.setattr(rdc, "running_processes", lambda: (live, []))
    monkeypatch.setattr(rdc, "emit_bus", lambda report: None)
    return rdc.run_cycle(allow_issues=False)


def _decl(script):
    return {script: {"name": Path(script).stem, "script": script,
                     "aliases": [], "declared_in": ["watchdog.sh:pgrep_guard"],
                     "kind": "daemon"}}


def test_run_cycle_reports_rotated_out_separately_from_missing(
        monkeypatch, tmp_path, bed):
    """END TO END, POSITIVE POLE. Named on the report AND the heartbeat AND the
    log -- an absence explained away silently is an unauditable check."""
    live = [_proc(24973, bed["rotator"]), _proc(41216, bed["hewho"])]
    r = _isolate(monkeypatch, tmp_path, _decl(bed["intent"]), live)

    assert r["counts"]["missing"] == 0
    assert r["counts"]["rotated_out"] == 1
    assert [d["name"] for d in r["rotated_out"]] == ["intent_engine_daemon"]

    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["healthy"] is True
    assert hb["rotated_out_names"] == ["intent_engine_daemon"]
    assert "ROTATED_OUT" in (tmp_path / "drift.log").read_text()


def test_run_cycle_still_reports_missing_when_rotation_is_broken(
        monkeypatch, tmp_path, bed):
    """END TO END, NEGATIVE POLE. Same declaration, same rotator, sibling DEAD:
    the report must go red. If this ever reads healthy the cure has eaten the
    check it was meant to sharpen."""
    live = [_proc(24973, bed["rotator"])]
    r = _isolate(monkeypatch, tmp_path, _decl(bed["intent"]), live)

    assert r["counts"]["rotated_out"] == 0
    assert r["counts"]["missing"] == 1
    assert r["missing"][0]["name"] == "intent_engine_daemon"
    assert json.loads((tmp_path / "hb.json").read_text())["healthy"] is False


def test_rotated_out_closes_the_open_issue_saying_which_exit_it_took(
        monkeypatch, tmp_path, bed):
    """THE LATCH for #5046. The lane already carries an open issue in state; the
    next clean cycle must close it, and the close comment must say ROTATED, not
    'running again' -- a false positive retired as a healed outage teaches the
    next reader the wrong thing."""
    closed = {}
    monkeypatch.setattr(rdc, "_gh",
                        lambda args: (closed.update({"args": args}), (0, ""))[1])
    live = [_proc(24973, bed["rotator"]), _proc(41216, bed["hewho"])]

    monkeypatch.setattr(rdc, "GO_SH", tmp_path / "nonexistent_go.sh")
    monkeypatch.setattr(rdc, "WATCHDOG_SH", tmp_path / "nonexistent_wd.sh")
    monkeypatch.setattr(rdc, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(rdc, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(rdc, "DRIFT_LOG", tmp_path / "drift.log")
    monkeypatch.setattr(rdc, "merge_declared", lambda a, b: _decl(bed["intent"]))
    monkeypatch.setattr(rdc, "running_processes", lambda: (live, []))
    monkeypatch.setattr(rdc, "emit_bus", lambda report: None)
    (tmp_path / "state.json").write_text(json.dumps({
        "consecutive_missing": {"intent_engine_daemon":
                                {"cycles": 18, "since": "2026-09-14T02:08:15+00:00"}},
        "issues": {"intent_engine_daemon": "5046"}}))

    rdc.run_cycle(allow_issues=True)

    assert closed["args"][0:2] == ["issue", "close"]
    assert closed["args"][2] == "5046"
    body = closed["args"][-1]
    assert "rotated out" in body
    assert "running again" not in body
    # and the state is drained, so the next cycle is a no-op (idempotent)
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["issues"] == {}
    assert st["consecutive_missing"] == {}


def test_an_orphaned_open_issue_is_still_closed(monkeypatch, tmp_path, bed):
    """The close path must be driven by `issues`, not by `consecutive_missing`.

    Any cycle that drops a name from consecutive_missing without closing --
    a `--no-issues` run is the obvious one, and the #5046 verification itself
    did exactly that to the live state file -- used to orphan the entry in
    `issues`, where nothing ever looked again. The lane recovers and the issue
    stays open forever.

    NEGATIVE CONTROL for this one is test_..._still_reports_missing_when_
    rotation_is_broken above: a name that IS still missing must NOT be closed.
    """
    closed = {}
    monkeypatch.setattr(rdc, "_gh",
                        lambda args: (closed.update({"args": args}), (0, ""))[1])
    live = [_proc(24973, bed["rotator"]), _proc(41216, bed["hewho"])]

    monkeypatch.setattr(rdc, "GO_SH", tmp_path / "nonexistent_go.sh")
    monkeypatch.setattr(rdc, "WATCHDOG_SH", tmp_path / "nonexistent_wd.sh")
    monkeypatch.setattr(rdc, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(rdc, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(rdc, "DRIFT_LOG", tmp_path / "drift.log")
    monkeypatch.setattr(rdc, "merge_declared", lambda a, b: _decl(bed["intent"]))
    monkeypatch.setattr(rdc, "running_processes", lambda: (live, []))
    monkeypatch.setattr(rdc, "emit_bus", lambda report: None)
    # the orphan: an OPEN issue with NO consecutive_missing entry behind it
    (tmp_path / "state.json").write_text(json.dumps({
        "consecutive_missing": {},
        "issues": {"intent_engine_daemon": "5046"}}))

    rdc.run_cycle(allow_issues=True)

    assert closed.get("args", [None, None])[0:2] == ["issue", "close"]
    assert closed["args"][2] == "5046"
    assert json.loads((tmp_path / "state.json").read_text())["issues"] == {}


def test_a_still_missing_lane_keeps_its_issue_open(monkeypatch, tmp_path, bed):
    """NEGATIVE CONTROL for the orphan sweep: reconciling from `issues` must not
    become a way to close an issue for a daemon that is still down."""
    calls = []
    monkeypatch.setattr(rdc, "_gh", lambda args: (calls.append(args), (0, ""))[1])
    live = [_proc(24973, bed["rotator"])]          # rotator up, NO child alive

    monkeypatch.setattr(rdc, "GO_SH", tmp_path / "nonexistent_go.sh")
    monkeypatch.setattr(rdc, "WATCHDOG_SH", tmp_path / "nonexistent_wd.sh")
    monkeypatch.setattr(rdc, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(rdc, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(rdc, "DRIFT_LOG", tmp_path / "drift.log")
    monkeypatch.setattr(rdc, "merge_declared", lambda a, b: _decl(bed["intent"]))
    monkeypatch.setattr(rdc, "running_processes", lambda: (live, []))
    monkeypatch.setattr(rdc, "emit_bus", lambda report: None)
    (tmp_path / "state.json").write_text(json.dumps({
        "consecutive_missing": {},
        "issues": {"intent_engine_daemon": "5046"}}))

    rdc.run_cycle(allow_issues=True)

    assert not any(a[0:2] == ["issue", "close"] for a in calls)
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["issues"] == {"intent_engine_daemon": "5046"}
