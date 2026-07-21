"""Tests for the deferred hatch + trend detector (CofC ruling 2026-07-21).

The ruling made --enforce satisfiable by letting a PR DECLARE an unmounted
router instead of mounting it. That hatch is only safe if its integrity checks
actually fire, so these tests exercise the failure paths, not the happy path:
a stale deferral, a reasonless deferral, a reasonless exemption. Each of those
is a way the gate could go decorative while still reporting green -- which is
precisely the failure the 2026-07-19 postmortem indicted.
"""
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATCHET = os.path.join(ROOT, "tools", "reachability_ratchet.py")
TREND = os.path.join(ROOT, "tools", "reachability_trend.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# entry parsing -- the shapes a human will actually write by hand
# --------------------------------------------------------------------------

def test_entries_accepts_every_hand_written_shape():
    m = _load(RATCHET, "reachability_ratchet")
    assert m._entries({"a": "because"}) == {"a": "because"}
    assert m._entries({"a": {"reason": "because", "date": "2026-07-21"}}) == {"a": "because"}
    assert m._entries([{"module": "a", "reason": "because"}]) == {"a": "because"}
    # a bare string carries NO reason -- this must survive as empty, because
    # that is exactly what the reasonless check is looking for
    assert m._entries(["a"]) == {"a": ""}


def test_bare_string_exemption_is_reasonless_not_silently_accepted():
    m = _load(RATCHET, "reachability_ratchet")
    assert m._entries(["legacy_thing"])["legacy_thing"] == ""


# --------------------------------------------------------------------------
# census bookkeeping
# --------------------------------------------------------------------------

def test_census_reports_the_deferred_partitions():
    m = _load(RATCHET, "reachability_ratchet")
    c = m.census()
    for key in ("deferred_active", "deferred_stale", "deferred_reasonless",
                "deferred_declared_count", "exempt_reasonless"):
        assert key in c, "census must report %s for the trend detector" % key
    # active and stale are disjoint by construction
    assert not (set(c["deferred_active"]) & set(c["deferred_stale"]))


def test_deferred_module_still_counts_as_an_orphan():
    """Declaring is not mounting. If a deferral removed the module from
    orphan_count the census would stop describing reality and the artifact
    would launder the number it exists to report."""
    m = _load(RATCHET, "reachability_ratchet")
    c = m.census()
    orphan_stems = {o["module"] for o in c["orphans"]}
    for stem in c["deferred_active"]:
        assert stem in orphan_stems


def test_stale_deferral_is_detected():
    """A deferral for something no longer an orphan inflates headroom."""
    m = _load(RATCHET, "reachability_ratchet")
    c = m.census()
    orphan_stems = {o["module"] for o in c["orphans"]}
    declared = set(c["deferred_active"]) | set(c["deferred_stale"])
    assert set(c["deferred_stale"]) == declared - orphan_stems


# --------------------------------------------------------------------------
# trend detector -- the alarms are the whole point
# --------------------------------------------------------------------------

def _rows(spec):
    out = []
    for i, (routers, mounted, added, madded) in enumerate(spec):
        out.append({
            "date": "2026-07-%02d" % (10 + i),
            "router_modules_total": str(routers),
            "mounted_count": str(mounted),
            "orphan_count": str(routers - mounted),
            "exempted_count": "0",
            "deferred_active_count": "0",
            "baseline": "276",
            "mode": "enforce",
            "routers_added": str(added),
            "mounts_added": str(madded),
            "deferred_added": "0",
        })
    return out


def test_stalled_mounting_alarm_fires_after_three_days():
    t = _load(TREND, "reachability_trend")
    rows = _rows([(300, 31, 10, 0), (310, 31, 10, 0), (320, 31, 10, 0)])
    fired = t.alarms(rows)
    assert any("STALLED MOUNTING" in a for a in fired)


def test_stalled_mounting_does_not_fire_when_something_gets_mounted():
    t = _load(TREND, "reachability_trend")
    rows = _rows([(300, 31, 10, 0), (310, 33, 10, 2), (320, 33, 10, 0)])
    assert not any("STALLED MOUNTING" in a for a in t.alarms(rows))


def test_exemption_inflation_alarm_fires():
    t = _load(TREND, "reachability_trend")
    rows = _rows([(300, 31, 10, 1), (310, 32, 10, 1)])
    rows[-1]["exempted_count"] = "12"
    assert any("EXEMPTION INFLATION" in a for a in t.alarms(rows))


def test_mode_regression_alarm_fires_if_enforce_gets_dropped():
    t = _load(TREND, "reachability_trend")
    rows = _rows([(300, 31, 10, 1)])
    rows[-1]["mode"] = "observe"
    assert any("MODE REGRESSION" in a for a in t.alarms(rows))


def test_healthy_trend_raises_nothing():
    t = _load(TREND, "reachability_trend")
    rows = _rows([(300, 31, 5, 1), (302, 33, 2, 2), (303, 35, 1, 2)])
    assert t.alarms(rows) == []


def test_first_row_has_empty_deltas_not_fabricated_zeros():
    """A fabricated 0 would read as 'no drift' and mis-seed the stall counter."""
    t = _load(TREND, "reachability_trend")
    row = t.build_row({"router_modules_total": 307, "mounted_count": 31,
                       "orphan_count": 276, "exempted_count": 0,
                       "deferred_active_count": 0, "baseline": 276,
                       "mode": "enforce"}, None)
    assert row["routers_added"] == ""
    assert row["mounts_added"] == ""


def test_append_then_read_roundtrips():
    t = _load(TREND, "reachability_trend")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "trend.csv")
        data = {"router_modules_total": 307, "mounted_count": 31,
                "orphan_count": 276, "exempted_count": 0,
                "deferred_active_count": 0, "baseline": 276, "mode": "enforce"}
        t.append_row(path, t.build_row(data, None))
        rows = t.read_log(path)
        assert len(rows) == 1 and rows[0]["router_modules_total"] == "307"
        data2 = dict(data, router_modules_total=317, mounted_count=31)
        t.append_row(path, t.build_row(data2, rows[0]))
        rows = t.read_log(path)
        assert len(rows) == 2
        assert rows[1]["routers_added"] == "10"
        assert rows[1]["mounts_added"] == "0"
