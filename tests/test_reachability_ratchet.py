"""Tests for the reachability ratchet (2026-07-19 postmortem).

Deliberately does NOT assert the orphan count equals the baseline. That check is
the ratchet's job, at the mode the ratchet is configured for. Asserting it here
would make every new orphan fail pr-gates -- enforcement through the back door,
while the builder still has no way to mount itself. Keep the two separate: this
file proves the mechanism is sound, the ratchet decides policy.
"""
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATCHET = os.path.join(ROOT, "tools", "reachability_ratchet.py")


def _load():
    spec = importlib.util.spec_from_file_location("reachability_ratchet", RATCHET)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reachability_ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_router_detection_matches_both_shapes():
    m = _load()
    assert m.ROUTER_DEF.search("router = APIRouter(prefix='/api')")
    assert m.ROUTER_DEF.search("@router.get('/x')\ndef x(): ...")
    assert m.ROUTER_DEF.search("@router.delete('/x')\ndef x(): ...")
    assert not m.ROUTER_DEF.search("def plain():\n    return 1\n")


def test_describe_extracts_the_mount_shape():
    m = _load()
    src = (
        "from fastapi import APIRouter\n"
        "from app.db import get_session\n"
        "router = APIRouter(prefix='/api/thing', tags=['thing'])\n"
        "@router.get('/list')\n"
        "def a(): ...\n"
        "@router.post('/new')\n"
        "def b(): ...\n"
    )
    d = m.describe("thing_api.py", src)
    assert d["module"] == "thing_api"
    assert d["route_count"] == 2
    assert "GET /list" in d["routes"] and "POST /new" in d["routes"]
    assert d["declared_prefix"] == "/api/thing"
    assert d["tags"] == ["thing"]
    assert d["imports_data_layer"] is True
    assert d["parses"] is True


def test_census_partitions_cleanly():
    m = _load()
    c = m.census()
    mounted, exempted = set(c["mounted"]), set(c["exempted"])
    orphans = {o["module"] for o in c["orphans"]}
    # the three buckets must be disjoint and sum to the total
    assert not (mounted & orphans)
    assert not (mounted & exempted)
    assert not (orphans & exempted)
    assert len(mounted) + len(orphans) + len(exempted) == c["router_modules_total"]
    assert c["orphan_count"] == len(orphans)


def test_baseline_file_is_valid():
    m = _load()
    base = m.load_baseline()
    assert base is not None, "tools/reachability_baseline.json must exist and hold an int"
    assert isinstance(base, int) and base >= 0


def test_known_mounted_module_is_not_counted_as_orphan():
    """verdict_breakdown_api is mounted in app/main.py -- the canary.

    If this ever flips to orphan, the mount-surface scan has broken, not the app.
    """
    m = _load()
    c = m.census()
    assert "verdict_breakdown_api" in set(c["mounted"])
