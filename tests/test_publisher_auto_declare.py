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


# --- G09: a NEW root-level router is STAGED, not declared ---------------------
#
# The deferred list stood at 62 against a cap of 40 and nothing drained it: a
# root-level router has no manifest and no promotion path. The publisher now
# routes a NEW root-level router into services/staged/<stem>/ with a [service]
# manifest (zo_sentinel/publisher/auto_stage.stage_root_router), so the promoter
# decides on evidence. Declaration remains the fallback for everything the
# redirect refuses. These pin both halves.

import shutil  # noqa: E402

from zo_sentinel.publisher import auto_stage  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_service_manifests as csm  # noqa: E402


def _files(result):
    files, _reason = result
    return dict(files or [])


def test_new_root_router_is_staged_with_a_valid_manifest(tmp_path):
    c = _clone(tmp_path)
    files = _files(auto_stage.stage_root_router(c, "thing_api.py", ROUTER_SRC))
    assert files["services/staged/thing_api/router.py"] == ROUTER_SRC
    toml = files["services/staged/thing_api/service.toml"]
    verdict, detail = csm.classify_source(toml, "thing_api")
    assert verdict == "OK", detail
    assert 'import_path = "services.active.thing_api.router"' in toml
    assert 'prefix = "/api/x"' in toml, "the router's own prefix is carried over"
    assert "services/staged/thing_api/__init__.py" in files


def test_staged_router_is_never_declared(tmp_path):
    """Once staged, the path is not root-level, so the ratchet's scope and the
    deferred file are both untouched -- the graveyard stops growing."""
    c = _clone(tmp_path)
    changed, detail = auto_declare.declare(
        c, "services/staged/thing_api/router.py", ROUTER_SRC)
    assert not changed and _read(tmp_path) == {}


def test_manifest_without_router_prefix_defaults_to_api(tmp_path):
    src = "from fastapi import APIRouter\nrouter = APIRouter()\n"
    files = _files(auto_stage.stage_root_router(_clone(tmp_path), "bare_api.py", src))
    assert 'prefix = "/api"' in files["services/staged/bare_api/service.toml"]


def test_existing_root_module_is_an_edit_not_staged(tmp_path):
    c = _clone(tmp_path)
    (c / "thing_api.py").write_text("old", encoding="utf-8")
    files, reason = auto_stage.stage_root_router(c, "thing_api.py", ROUTER_SRC)
    assert files is None and "edit" in reason


def test_mounted_root_router_is_not_staged(tmp_path):
    files, reason = auto_stage.stage_root_router(
        _clone(tmp_path), "mounted_thing_api.py", ROUTER_SRC)
    assert files is None and "app/" in reason


def test_active_name_collision_is_not_staged(tmp_path):
    c = _clone(tmp_path)
    (c / "services" / "active" / "thing_api").mkdir(parents=True)
    files, reason = auto_stage.stage_root_router(c, "thing_api.py", ROUTER_SRC)
    assert files is None and "collide" in reason


def test_non_router_and_nested_paths_are_not_staged(tmp_path):
    c = _clone(tmp_path)
    assert auto_stage.stage_root_router(c, "plain.py", "def f(): return 1\n")[0] is None
    assert auto_stage.stage_root_router(c, "app/routers/x.py", ROUTER_SRC)[0] is None
    assert auto_stage.stage_root_router(c, "view.html", ROUTER_SRC)[0] is None


def test_existing_staged_manifest_is_not_overwritten(tmp_path):
    c = _clone(tmp_path)
    d = c / "services" / "staged" / "thing_api"
    d.mkdir(parents=True)
    (d / "service.toml").write_text("human", encoding="utf-8")
    files = _files(auto_stage.stage_root_router(c, "thing_api.py", ROUTER_SRC))
    assert "services/staged/thing_api/service.toml" not in files
    assert "services/staged/thing_api/router.py" in files


def test_stage_root_router_never_raises():
    files, reason = auto_stage.stage_root_router(None, None, None)
    assert files is None and isinstance(reason, str)


def test_staged_router_gets_its_missing_manifest(tmp_path):
    c = _clone(tmp_path)
    out = dict(auto_stage.staged_companions(
        c, "services/staged/risk_axis/router.py", ROUTER_SRC))
    verdict, detail = csm.classify_source(
        out["services/staged/risk_axis/service.toml"], "risk_axis")
    assert verdict == "OK", detail
    assert auto_stage.staged_companions(c, "services/active/x/router.py", ROUTER_SRC) == []
    assert auto_stage.staged_companions(c, "services/staged/x/models.py", ROUTER_SRC) == []


def test_fix_manifests_runs_the_real_gate(tmp_path):
    c = _clone(tmp_path)
    shutil.copy(os.path.join(ROOT, "tools", "check_service_manifests.py"),
                str(c / "tools" / "check_service_manifests.py"))
    d = c / "services" / "staged" / "flat_svc"
    d.mkdir(parents=True)
    # FLAT: valid TOML, no [service] header -- the shape --fix reshapes.
    (d / "service.toml").write_text(
        'name = "flat_svc"\nimport_path = "services.active.flat_svc.router"\n'
        'prefix = "/api"\ntag = "flat_svc"\n', encoding="utf-8")
    ok, detail = auto_stage.fix_manifests(c, ["services/staged/flat_svc/service.toml"])
    assert ok, detail
    assert csm.classify(str(d / "service.toml"))[0] == "OK"


def test_fix_manifests_skips_cleanly_without_the_tool(tmp_path):
    ok, detail = auto_stage.fix_manifests(tmp_path, ["services/staged/x/service.toml"])
    assert ok and "skipped" in detail
