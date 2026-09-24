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


# --- DEFERRED NON-INCREASING legibility (#3944, chairman ruling 2026-09-15) ---
#
# The rule was MUTE in its steady state. Measured on a clean export of main on
# 2026-09-15: now=62, baseline=62, equal -> the rule compared, held, and printed
# nothing. A disabled rule prints nothing too, so no CI log could tell the two
# apart, and the fetch-depth:2 cure for the shallow-checkout skip stayed
# unverifiable for 21 days across three lanes.
#
# These assert the LINE, not the verdict. The verdict is the ratchet's job (see
# this file's docstring); what is asserted here is that the operands are always
# stated. The GREW pole is the negative control: the same probe must catch a
# rule that is genuinely breached, or it proves nothing about the held pole.


def test_deferred_rule_always_names_its_operands():
    """Every state emits a line. Silence is the defect, at any outcome."""
    m = _load()
    cases = [
        (62, [(62, "baseline file")]),
        (63, [(62, "baseline file")]),
        (61, [(62, "baseline file")]),
        (62, []),
        (62, [(62, "baseline file"), (62, "previous commit")]),
    ]
    for now_n, refs in cases:
        line, outcome = m.deferred_rule_line(now_n, refs)
        assert line.strip(), "the rule emitted nothing for now=%r refs=%r" % (now_n, refs)
        assert "DEFERRED NON-INCREASING" in line
        assert "now=%d" % now_n in line, line
        assert outcome in ("SKIPPED", "GREW", "HELD", "SHRANK")


def test_deferred_rule_distinguishes_compared_from_skipped():
    """The two readings are OPPOSITE: held means gated, skipped means ungated.

    This is the whole of #3944. Before the fix both rendered as an empty string.
    """
    m = _load()
    held, held_outcome = m.deferred_rule_line(62, [(62, "baseline file")])
    skipped, skipped_outcome = m.deferred_rule_line(62, [])

    assert held_outcome == "HELD" and skipped_outcome == "SKIPPED"
    assert "COMPARED" in held and "SKIPPED" not in held
    assert "SKIPPED" in skipped and "COMPARED" not in skipped
    assert held != skipped, "a held rule and a disabled rule must not read alike"
    # the skipped branch must say growth is unguarded, not merely stay quiet
    assert "UNGATED" in skipped


def test_deferred_rule_reports_both_operands_and_every_reference():
    """limit, now, and WHICH references were reachable.

    'previous commit' appearing is how a run proves HEAD~1 was fetched -- the
    check the chairman could not make on 2026-09-15 because nothing was printed.
    """
    m = _load()
    line, outcome = m.deferred_rule_line(
        62, [(62, "baseline file"), (62, "previous commit")])
    assert "now=62" in line and "limit=62" in line
    assert "baseline file=62" in line and "previous commit=62" in line
    assert outcome == "HELD"

    # with only the baseline reachable, the line must NOT claim HEAD~1 was seen
    shallow, _ = m.deferred_rule_line(62, [(62, "baseline file")])
    assert "previous commit" not in shallow, shallow


def test_deferred_rule_negative_control_grew_is_still_loud():
    """The GREW pole. A probe that never sees a breach proves nothing (R4)."""
    m = _load()
    line, outcome = m.deferred_rule_line(63, [(62, "baseline file")])
    assert outcome == "GREW"
    assert "-> GREW" in line
    assert "now=63" in line and "limit=62" in line


def test_deferred_rule_takes_the_stricter_of_two_references():
    """min() over refs -- an unchanged behaviour, pinned so the refactor is safe."""
    m = _load()
    line, outcome = m.deferred_rule_line(
        62, [(62, "baseline file"), (58, "previous commit")])
    assert outcome == "GREW", line
    assert "limit=58 (previous commit)" in line, line
