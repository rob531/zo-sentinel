"""schema/external_planes.json must be a ratchet, never an allowlist.

WHY THE DISTINCTION IS THE WHOLE FILE
    `gate_checks` was on the #4080 phantom-table list. It is a real table. It
    lives in gate_errors.db, a standalone DuckDB file the gate framework keeps
    deliberately apart from the write-service bus, created by
    tests/gate_errors_bootstrap.py and read by tools/governor_explain.py.
    referent_verify's catalog covers three planes -- bus, app/models.py,
    migrations/ -- and that file is none of them. The table was real; the plane
    was invisible.

    The obvious fix, a list of names to stop complaining about, is the thing a
    blocking gate must never grow. An allowlist says "trust me" once and then
    keeps saying it after it stops being true, which is a silent hole in
    exactly the check whose entire purpose is that nothing goes unchecked
    silently.

    So every entry carries PROVENANCE -- the file that creates the table and
    the text proving the CREATE is still in it -- and referent_verify re-checks
    that claim on every run. These tests hold the three properties that make
    the difference real:

      1. a VALID claim admits the table
      2. a claim whose PROOF has gone does NOT admit the table, and reports
      3. a claim whose CREATOR has gone does NOT admit the table, and reports

    Property 2 and 3 are the ratchet. Without them this file is an allowlist
    with extra steps.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DECL = ROOT / "schema" / "external_planes.json"


def _rv():
    spec = importlib.util.spec_from_file_location(
        "referent_verify", ROOT / "tools" / "referent_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rv():
    return _rv()


def _write(rv_mod, tmp_path, decl):
    p = tmp_path / "external_planes.json"
    p.write_text(json.dumps(decl))
    rv_mod.EXTERNAL_PLANES = p
    return rv_mod.load_catalog()


def test_committed_declaration_is_valid_right_now(rv):
    """The declaration in this repo must be true of this repo."""
    _t, meta, _r = rv.load_catalog()
    assert meta["external_plane_problems"] == [], \
        f"schema/external_planes.json makes a claim that is no longer true: " \
        f"{meta['external_plane_problems']}"


def test_gate_checks_is_admitted_by_the_valid_claim(rv):
    tables, _m, _r = rv.load_catalog()
    assert "gate_checks" in tables


def test_every_declared_table_names_a_reader(rv):
    """An entry with no reader is an entry nobody needs -- delete it instead."""
    decl = json.loads(DECL.read_text())
    for plane in decl["planes"]:
        for tname, tinfo in plane["tables"].items():
            assert tinfo.get("read_by"), f"{tname} declares no read_by"


def test_a_broken_proof_does_not_admit_the_table(rv, tmp_path):
    """THE RATCHET. A stale claim must lose its table, not keep it."""
    decl = json.loads(DECL.read_text())
    decl["planes"][0]["tables"]["gate_checks"]["proof"] = \
        "CREATE TABLE IF NOT EXISTS gate_checks_THAT_MOVED"
    tables, meta, _r = _write(rv, tmp_path, decl)
    assert "gate_checks" not in tables
    assert any("NO LONGER in" in p for p in meta["external_plane_problems"])


def test_a_vanished_creator_does_not_admit_the_table(rv, tmp_path):
    decl = json.loads(DECL.read_text())
    decl["planes"][0]["created_by"] = "tests/this_file_was_deleted.py"
    tables, meta, _r = _write(rv, tmp_path, decl)
    assert "gate_checks" not in tables
    assert any("does not exist" in p for p in meta["external_plane_problems"])


def test_a_claim_with_no_proof_is_rejected(rv, tmp_path):
    """'Trust me' is the allowlist form, and it must not work."""
    decl = {"planes": [{"plane": "x", "created_by": "tools/referent_verify.py",
                        "tables": {"totally_made_up": {}}}]}
    tables, meta, _r = _write(rv, tmp_path, decl)
    assert "totally_made_up" not in tables
    assert any("declares no proof" in p for p in meta["external_plane_problems"])


def test_a_claim_with_no_creator_is_rejected(rv, tmp_path):
    decl = {"planes": [{"plane": "x",
                        "tables": {"totally_made_up": {"proof": "CREATE TABLE"}}}]}
    tables, meta, _r = _write(rv, tmp_path, decl)
    assert "totally_made_up" not in tables
    assert any("no created_by" in p for p in meta["external_plane_problems"])


def test_absent_declaration_file_is_not_an_error(rv, tmp_path):
    """Optional, and its absence is not a failure -- just no external plane."""
    rv.EXTERNAL_PLANES = tmp_path / "nope.json"
    tables, meta, reason = rv.load_catalog()
    assert reason is None
    assert meta["external_plane_problems"] == []
    assert "gate_checks" not in tables
