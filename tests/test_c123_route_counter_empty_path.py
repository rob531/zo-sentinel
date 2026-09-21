"""c123: a zero-length route path is a route, and reading the census may not write it.

THE DEFECT, measured on origin/main @ 0f5d7ae0e (2026-09-21) by RUNNING
tools/reachability_ratchet.py over the real tree -- not by scanning for its
conditions:

    router modules 367 | mounted 32 | exempt 0 | ORPHANED 335
    orphans whose route_count changes when the regex is fixed: 3
      audit_log_api                     0 -> 2   [NO_ROUTES verdict FLIPS]
      mcp_score_dispute_api             3 -> 5
      sentinel_ui_inventory_paginator   1 -> 2

`describe()` extracted route paths with `[\"']([^\"']+)`. The `+` requires at
least one character, so `@router.get("")` -- legal FastAPI, resolving to the
router's own prefix -- matched NOTHING and was counted as zero routes.
audit_log_api.py declares `APIRouter(prefix="/audit-log")` and both of its
routes that way (POST "" line 49, GET "" line 99). It measured route_count=0,
tools/orphanage.py classified it NO_ROUTES ("probably not a service: remit
candidate"), and it was consequently nominated for DELETION -- for having no
routes, while serving two.

The two halves of this file are the same failure wearing two faces: an
instrument that misreports, and an instrument that mutates what it claims only
to read. tools/orphanage.py's docstring and --help both said "read-only" while
every invocation overwrote orphanage/manifest.json -- so the wrong verdict
above was persisted to disk by the act of looking at it.

Every assertion below has a NEGATIVE CONTROL: the OLD regex is applied to the
SAME input and observed producing the wrong answer, because an assertion never
seen red is an untested branch, not evidence (doctrine R4).

Pure stdlib, no network, no git, no census. Must pass on Windows and
ubuntu-latest.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RATCHET = os.path.join(ROOT, "tools", "reachability_ratchet.py")

# The exact regex this PR replaced. Kept HERE, in the test, so the negative
# control is self-contained: it cannot go green by accident when someone edits
# the tool, because it is not read from the tool.
OLD_ROUTE_RE = re.compile(r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)")

EMPTY_PATH_MODULE = (
    "from fastapi import APIRouter\n"
    "from app.db import get_session\n"
    'router = APIRouter(prefix="/audit-log", tags=["audit-log"])\n'
    '@router.post("", response_model=AuditEvent)\n'
    "def create(): ...\n"
    '@router.get("", response_model=list[AuditEvent])\n'
    "def listing(): ...\n"
)


def _ratchet():
    spec = importlib.util.spec_from_file_location("reachability_ratchet", RATCHET)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reachability_ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. the counter -- the assertion the ruling asks for, at both poles
# --------------------------------------------------------------------------

def test_a_module_whose_routes_are_all_empty_paths_has_routes():
    """audit_log_api.py in miniature. THIS is the test that fails on old main."""
    m = _ratchet()
    d = m.describe("audit_log_api.py", EMPTY_PATH_MODULE)

    assert d["route_count"] >= 1, (
        "a module declaring @router.get(\"\") must be counted as having at "
        "least one route -- got route_count=%r" % d["route_count"])
    assert d["route_count"] == 2
    assert d["declared_prefix"] == "/audit-log"

    # NEGATIVE CONTROL: the OLD regex on the SAME source, observed wrong.
    assert OLD_ROUTE_RE.findall(EMPTY_PATH_MODULE) == [], (
        "control failed: the old regex was supposed to find no routes here -- "
        "if it finds some, this fixture is not reproducing the defect")


def test_the_empty_path_is_rendered_not_dropped():
    """route_count is the gate; the census still has to be readable."""
    m = _ratchet()
    d = m.describe("audit_log_api.py", EMPTY_PATH_MODULE)
    assert 'POST ""' in d["routes"] and 'GET ""' in d["routes"], d["routes"]
    # no entry may be a bare verb with a dangling separator
    for r in d["routes"]:
        assert r.split(" ", 1)[1].strip(), "empty path rendered as nothing: %r" % r


@pytest.mark.parametrize("src,expected", [
    ('@router.get("")\n', ['GET ""']),
    ("@router.get('')\n", ['GET ""']),
    ('@router.get("/foo")\n', ["GET /foo"]),
    ('@router.put("/a/{id}")\n', ["PUT /a/{id}"]),
    ('@router.post(\n    "",\n    response_model=X,\n)\n', ['POST ""']),
    ('@router.delete("/x")\n@router.patch("")\n', ["DELETE /x", 'PATCH ""']),
])
def test_non_empty_paths_still_match_exactly_as_before(src, expected):
    """The fix may not be bought by loosening anything else."""
    m = _ratchet()
    assert m.describe("x.py", src)["routes"] == expected


def test_a_non_literal_path_is_still_not_a_route():
    """`*` must not start matching things `+` correctly refused."""
    m = _ratchet()
    assert m.describe("x.py", "@router.get(path)\ndef f(): ...\n")["route_count"] == 0
    assert m.describe("x.py", "def get(self): return 1\n")["route_count"] == 0


def test_the_house_shape_test_still_holds():
    """Regression guard for tests/test_reachability_ratchet.py's fixture."""
    m = _ratchet()
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
    assert d["route_count"] == 2
    assert "GET /list" in d["routes"] and "POST /new" in d["routes"]


def test_route_count_zero_is_what_orphanage_calls_no_routes():
    """The causal link, pinned. This is why the undercount cost a module.

    Asserted against the REAL classifier, so the two cannot drift apart.
    """
    from tools import orphanage  # noqa: E402
    m = _ratchet()
    d = m.describe("audit_log_api.py", EMPTY_PATH_MODULE)

    assert orphanage.classify(d, set(), {}) != "NO_ROUTES"

    # NEGATIVE CONTROL: the shape the OLD regex produced for this same module
    # -- route_count 0 -- is classified NO_ROUTES, i.e. "remit candidate".
    as_measured_before = dict(d, route_count=0, routes=[])
    assert orphanage.classify(as_measured_before, set(), {}) == "NO_ROUTES"


# --------------------------------------------------------------------------
# 2. orphanage.py is read-only IN FACT, not only in its docstring
# --------------------------------------------------------------------------

def _stub_manifest():
    return {"orphan_count": 1, "by_why": {"MOUNTABLE": 1}, "legend": {},
            "orphans": [{"module": "x_api", "why_unmounted": "MOUNTABLE",
                         "route_count": 1, "declared_prefix": "/x",
                         "imports_data_layer": True, "lines": 10,
                         "casing_autofixable": None,
                         "origin": {"commit": "abc", "date": "2026-01-01",
                                    "author": "a", "subject": "s"}}]}


@pytest.fixture()
def orphanage_on_tmp(tmp_path, monkeypatch):
    """Real main(), stubbed census, manifest redirected off the real tree."""
    from tools import orphanage  # noqa: E402
    out_dir = tmp_path / "orphanage"
    monkeypatch.setattr(orphanage, "OUT_DIR", str(out_dir))
    monkeypatch.setattr(orphanage, "OUT", str(out_dir / "manifest.json"))
    monkeypatch.setattr(orphanage, "build", _stub_manifest)
    return orphanage, out_dir / "manifest.json"


def test_default_invocation_writes_nothing(orphanage_on_tmp, capsys):
    """`python tools/orphanage.py` -- the documented read-only invocation."""
    orphanage, manifest = orphanage_on_tmp
    assert orphanage.main([]) == 0
    assert not manifest.exists(), (
        "orphanage.py claims read-only but wrote %s" % manifest)
    assert "NOT written" in capsys.readouterr().out


def test_json_and_top_are_also_read_only(orphanage_on_tmp):
    """Every non---refresh path. A read-only default with a writing sibling
    flag is not read-only."""
    orphanage, manifest = orphanage_on_tmp
    assert orphanage.main(["--json"]) == 0
    assert orphanage.main(["--top", "3"]) == 0
    assert not manifest.exists()


def test_refresh_still_writes_the_same_manifest(orphanage_on_tmp, capsys):
    """Backward compatibility: the write is moved, not removed.

    This is the GREEN pole of the control above -- a test that only ever sees
    the file absent cannot tell "read-only" from "broken".
    """
    orphanage, manifest = orphanage_on_tmp
    assert orphanage.main(["--refresh"]) == 0
    assert manifest.exists()
    assert json.loads(manifest.read_text(encoding="utf-8")) == _stub_manifest()
    assert "REWRITTEN" in capsys.readouterr().out


def test_refresh_creates_the_output_directory(orphanage_on_tmp):
    """os.makedirs moved inside the branch; it must still run there."""
    orphanage, manifest = orphanage_on_tmp
    assert not manifest.parent.exists()
    assert orphanage.main(["--refresh"]) == 0
    assert manifest.parent.is_dir()
