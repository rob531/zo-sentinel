"""test_bus_write_ack_census.py -- the enqueue-receipt guard census.

Hermetic: builds a fake module roster on disk and asserts the guarded-vs-direct
split, so the numbers bus_write_ack_census publishes (how many live writers still
count a /write 2xx as a stored row) cannot silently rot.

This test is the tool's only CI subscriber: dark_tools.py drops tests/ from its
caller scan by design, so the tool reads DARK there and carries an EXPECTED_DARK
entry pointing back here. If this file is renamed or stops exercising the tool,
that exemption is stale -- keep the two poles below (a guarded writer and an
unguarded one) exercised.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "tools" / "bus_write_ack_census.py"
_spec = importlib.util.spec_from_file_location("bus_write_ack_census", _SRC)
bwac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bwac)


def _mkroster(tmp_path):
    """A guarded writer, a direct writer, and a non-writer -- the three classes."""
    guarded = tmp_path / "guarded.py"
    guarded.write_text(
        "from bus_write_guard import guarded_write\n"
        'row = {"table": "signals"}\n',
        encoding="utf-8",
    )
    direct = tmp_path / "direct.py"
    direct.write_text(
        'resp = requests.post("/write", json={"table": "signals"})\n',
        encoding="utf-8",
    )
    nowrite = tmp_path / "nowrite.py"
    nowrite.write_text('print("no bus write here")\n', encoding="utf-8")
    return guarded, direct, nowrite


def test_classify_splits_guarded_from_direct(tmp_path):
    guarded, direct, nowrite = _mkroster(tmp_path)
    g, u = bwac.classify([guarded, direct, nowrite])
    # Two poles: the guarded writer is NOT counted unguarded, the direct one is.
    assert guarded in g and guarded not in u
    assert direct in u and direct not in g
    # A module that never writes to the bus is in neither list.
    assert nowrite not in g and nowrite not in u


def test_main_roster_file_is_a_census_rc0(tmp_path):
    guarded, direct, nowrite = _mkroster(tmp_path)
    roster = tmp_path / "roster.txt"
    roster.write_text("\n".join(str(p) for p in (guarded, direct, nowrite)), encoding="utf-8")
    # Plain census exits 0 whatever it finds.
    assert bwac.main(["--roster-from", str(roster)]) == 0
    # --require-zero turns the standing unguarded writer into a non-zero exit.
    assert bwac.main(["--roster-from", str(roster), "--require-zero"]) == 1


def test_empty_roster_is_unknown_not_zero_rc2(tmp_path):
    # R6: a roster that cannot be read is UNKNOWN (rc=2), never a clean 0 census.
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    assert bwac.main(["--roster-from", str(empty)]) == 2
