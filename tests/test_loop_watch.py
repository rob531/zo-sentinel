"""loop_watch.assess -- pure stall-localization logic (no I/O; runs in CI)."""
import importlib.util, pathlib
from datetime import datetime, timezone, timedelta
_spec = importlib.util.spec_from_file_location(
    "lw", pathlib.Path(__file__).resolve().parents[1] / "loop_watch.py")
LW = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(LW)

NOW = datetime(2026, 6, 22, 13, 0, tzinfo=timezone.utc)
def iso(mins_ago): return (NOW - timedelta(minutes=mins_ago)).isoformat()

def _sig(**kw):
    base = dict(now=NOW.isoformat(), repo_head="abc12345deadbeef", graph_commit="abc12345deadbeef",
                memory={"directive_proposed": iso(5)}, proposed_newest=iso(5),
                proposed_count=3, goose=[{"rc": "0", "timeout": False, "delta": "2"}])
    base.update(kw); return base

def test_healthy_loop_flows():
    v = LW.assess(_sig())
    assert v["overall"] == "ok" and v["stall"] is None
    assert all(s == "ok" for s in v["stages"].values())

def test_graph_behind_repo_is_alert():
    v = LW.assess(_sig(graph_commit="999999999999"))   # graph commit != repo head
    assert v["stages"]["graph"] == "stale" and v["stall"] == "graph" and v["overall"] == "alert"

def test_plus0_localizes_to_directive_stage():
    # graph + memory fresh, but no new proposal for hours == the +0 stall
    v = LW.assess(_sig(proposed_newest=iso(600), memory={"build_artifact": iso(3)}))
    assert v["stages"]["directive"] == "stale" and v["stall"] == "directive" and v["overall"] == "warn"

def test_all_timeouts_is_goose_hang():
    v = LW.assess(_sig(goose=[{"rc": "timeout", "timeout": True}] * 3))
    assert v["stages"]["goose"] == "hung"

def test_first_broken_stage_wins_localization():
    # both graph behind AND directive stale -> localize the EARLIEST (graph)
    v = LW.assess(_sig(graph_commit="zzz", proposed_newest=iso(999)))
    assert v["stall"] == "graph"
