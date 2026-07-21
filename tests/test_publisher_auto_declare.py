"""Tests for publisher-side auto-declaration (CofC 2026-07-21 follow-through).

Arming the ratchet exposed that the deferred hatch was satisfiable only by a
human: the publisher writes exactly one file per PR, so an autonomous build
could neither mount its router nor declare it. These tests pin the properties
that keep the fix from becoming a laundering mechanism -- most importantly that
a declared module is STILL an orphan, and that nothing outside a root-level
router module ever gets declared (a spurious entry would read as STALE on the
next run and fail the gate, the exact opposite of the intent).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zo_sentinel.publisher import auto_declare  # noqa: E402

ROUTER_SRC = (
    "from fastapi import APIRouter\n"
    "router = APIRouter(prefix='/api/x', tags=['x'])\n"
    "@router.get('/y')\n"
    "def y(): ...\n"
)


def _clone(tmp_path, deferred=None):
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "reachability_deferred.json").write_text(
        json.dumps({"deferred": deferred or {}, "note": "test"}), encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(
        "from mounted_thing_api import router\n", encoding="utf-8")
    return tmp_path


def _read(tmp_path):
    return json.loads(
        (tmp_path / "tools" / "reachability_deferred.json").read_text(encoding="utf-8")
    )["deferred"]


# --- what SHOULD be declared -------------------------------------------------

def test_new_root_router_is_declared_with_a_reason(tmp_path):
    c = _clone(tmp_path)
    changed, _ = auto_declare.declare(c, "thing_api.py", ROUTER_SRC, task="build_thing")
    assert changed
    d = _read(tmp_path)
    assert "thing_api" in d
    assert d["thing_api"].strip(), "a declaration without a reason fails the gate"
    assert "build_thing" in d["thing_api"]


def test_declaration_is_idempotent(tmp_path):
    c = _clone(tmp_path, {"thing_api": "already here"})
    changed, detail = auto_declare.declare(c, "thing_api.py", ROUTER_SRC)
    assert not changed and "already declared" in detail
    assert _read(tmp_path)["thing_api"] == "already here", "must not overwrite a human reason"


def test_existing_entries_survive(tmp_path):
    c = _clone(tmp_path, {"older_api": "human reason"})
    auto_declare.declare(c, "thing_api.py", ROUTER_SRC)
    d = _read(tmp_path)
    assert d["older_api"] == "human reason" and "thing_api" in d


# --- what MUST NOT be declared ----------------------------------------------

def test_mounted_router_is_not_declared(tmp_path):
    """Declaring a mounted module would go STALE next run and fail the gate."""
    c = _clone(tmp_path)
    changed, detail = auto_declare.declare(c, "mounted_thing_api.py", ROUTER_SRC)
    assert not changed and "already mounted" in detail
    assert _read(tmp_path) == {}


def test_non_router_module_is_not_declared(tmp_path):
    c = _clone(tmp_path)
    changed, _ = auto_declare.declare(c, "plain_script.py", "def f():\n    return 1\n")
    assert not changed and _read(tmp_path) == {}


def test_nested_path_is_not_declared(tmp_path):
    """The ratchet scans root-level only; app/routers/x.py is out of scope."""
    c = _clone(tmp_path)
    changed, _ = auto_declare.declare(c, "app/routers/x.py", ROUTER_SRC)
    assert not changed and _read(tmp_path) == {}


def test_html_artifact_is_not_declared(tmp_path):
    c = _clone(tmp_path)
    changed, _ = auto_declare.declare(c, "some_view.html", ROUTER_SRC)
    assert not changed and _read(tmp_path) == {}


# --- failure behaviour -------------------------------------------------------

def test_missing_deferred_file_is_a_clean_skip(tmp_path):
    """A clone without the ratchet armed must publish normally, not crash."""
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    changed, detail = auto_declare.declare(tmp_path, "thing_api.py", ROUTER_SRC)
    assert not changed and "no deferred file" in detail


def test_malformed_deferred_file_is_left_alone(tmp_path):
    c = _clone(tmp_path)
    (c / "tools" / "reachability_deferred.json").write_text(
        json.dumps({"deferred": ["not", "a", "dict"]}), encoding="utf-8")
    changed, detail = auto_declare.declare(c, "thing_api.py", ROUTER_SRC)
    assert not changed and "unexpected shape" in detail


def test_declare_never_raises(tmp_path):
    """Losing a declaration flags the PR (loud, correct); losing the artifact
    would not be. So declare() swallows everything."""
    changed, detail = auto_declare.declare("/nonexistent/clone", "x_api.py", ROUTER_SRC)
    assert changed is False and isinstance(detail, str)


def test_router_detection_matches_the_ratchet_shapes():
    assert auto_declare.is_router_module("a_api.py", "router = APIRouter()")
    assert auto_declare.is_router_module("a_api.py", "@router.post('/x')\ndef x(): ...")
    assert not auto_declare.is_router_module("a_api.py", "def x(): return 1")
