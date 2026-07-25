"""Tests for the app-surface KL (FU-071).

Mirrors the discipline of tests/test_reachability_ratchet.py: prove the MECHANISM,
never assert a live count. A test that pinned `duplicate_paths == 22` would go red
on the next honest build and teach everyone to ignore it.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "app_surface_kl.py")


def _load():
    spec = importlib.util.spec_from_file_location("app_surface_kl", MOD)
    m = importlib.util.module_from_spec(spec)
    sys.modules["app_surface_kl"] = m
    spec.loader.exec_module(m)
    return m


ROUTER_SRC = (
    "from fastapi import APIRouter, Depends\n"
    "from app.db import get_session\n"
    "router = APIRouter(prefix='/api/widget', tags=['widget'])\n"
    "@router.get('/list')\n"
    "def a(): ...\n"
)


def test_static_stem_truncates_at_first_param():
    m = _load()
    assert m.static_stem("/api/verdict/{server_id}") == "/api/verdict"
    assert m.static_stem("/api/servers") == "/api/servers"
    assert m.static_stem("/{x}") == "/"
    assert m.static_stem("") == "/"


def test_join_composes_prefix_and_path():
    m = _load()
    assert m._join("/api/x", "/list") == "/api/x/list"
    assert m._join("/api/x/", "list") == "/api/x/list"
    assert m._join(None, "/list") == "/list"
    assert m._join("/api/x", "") == "/api/x"


def test_kl_has_all_four_sections_and_is_json_safe():
    import json
    m = _load()
    kl = m.build_app_surface_kl()
    for section in ("meta", "routes", "mounts", "consumers", "data"):
        assert section in kl, section
    assert kl["meta"]["kl_version"] == 1
    json.dumps(kl)          # must round-trip -- it is persisted as an artifact


def test_mounted_split_agrees_with_the_armed_ratchet():
    """One definition of "mounted". If these ever diverge, the KL would teach the
    architect something CI then punishes it for."""
    m = _load()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import reachability_ratchet
    census = reachability_ratchet.census()
    kl = m.build_app_surface_kl()
    assert kl["mounts"]["mounted"] == sorted(census["mounted"])
    assert kl["mounts"]["unmounted_count"] == census["orphan_count"]
    assert kl["mounts"]["router_modules_total"] == census["router_modules_total"]


def test_lint_flags_a_router_with_no_prefix():
    m = _load()
    kl = m.build_app_surface_kl()
    src = "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/x')\ndef a(): ...\n"
    codes = [c for c, _ in m.lint_source(src, kl, "brand_new_thing")]
    assert "NO_PREFIX" in codes


def test_lint_flags_a_duplicate_route():
    m = _load()
    kl = m.build_app_surface_kl()
    taken = kl["routes"]["taken_paths"]
    assert taken, "expected the repo to declare at least one route"
    method, path = taken[0].split(" ", 1)
    src = ("from fastapi import APIRouter\nrouter = APIRouter(prefix='')\n"
           "@router.%s('%s')\ndef a(): ...\n" % (method.lower(), path))
    codes = [c for c, _ in m.lint_source(src, kl, "some_new_module")]
    assert "DUPLICATE_ROUTE" in codes


def test_lint_is_silent_on_a_non_router_module():
    m = _load()
    kl = m.build_app_surface_kl()
    assert m.lint_source("def helper(x):\n    return x + 1\n", kl, "helper_mod") == []


def test_lint_does_not_flag_a_module_against_itself():
    """Re-linting an already-indexed module must not report it as its own duplicate."""
    m = _load()
    kl = m.build_app_surface_kl()
    mounted = kl["mounts"]["mounted"]
    assert mounted
    stem = mounted[0]
    src = open(os.path.join(ROOT, stem + ".py"), encoding="utf-8", errors="replace").read()
    codes = [c for c, _ in m.lint_source(src, kl, stem)]
    assert "DUPLICATE_ROUTE" not in codes


def test_a_shared_namespace_root_is_not_a_collision():
    """`/api` is claimed by ~65 modules and `/servers` by ~13. Those are namespace
    roots working correctly -- the routers under them differ by path. Flagging them
    would make the linter fire on almost every module and be ignored within a day."""
    m = _load()
    kl = m.build_app_surface_kl()
    src = ("from fastapi import APIRouter\nrouter = APIRouter(prefix='/api')\n"
           "@router.get('/some-brand-new-unclaimed-path')\ndef a(): ...\n")
    codes = [c for c, _ in m.lint_source(src, kl, "a_new_api_module")]
    assert "PREFIX_COLLISION" not in codes
    assert "NO_PREFIX" not in codes


def test_architect_block_respects_its_budget():
    m = _load()
    kl = m.build_app_surface_kl()
    block = m.render_for_architect(kl, budget=1500)
    assert len(block) <= 1500
    assert "APP SURFACE" in block


def test_architect_block_names_the_live_routes():
    m = _load()
    kl = m.build_app_surface_kl()
    block = m.render_for_architect(kl, budget=20000)
    live = [p for p, r in kl["routes"]["by_path"].items() if r["mounted"]]
    assert live
    assert live[0] in block


def test_root_module_self_test_apps_do_not_pollute_the_namespace():
    """`@app.get` at repo root is a __main__ self-test harness, not a served route."""
    m = _load()
    kl = m.build_app_surface_kl()
    for path, rec in kl["routes"]["by_path"].items():
        if rec["mounted"]:
            assert rec["module"].startswith("app/") or "/" not in rec["module"]
