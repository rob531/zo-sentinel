"""Tests for the Option-B spine reference generator (FU-072, report-only).

Same discipline as the ratchet/KL suites: prove the MECHANISM, never pin a live
count. A test asserting `broken_count == 4` would go red the moment a duplicate is
triaged and teach everyone to ignore it.
"""
import ast
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "tools", "spine_manifest.py")


def _load():
    spec = importlib.util.spec_from_file_location("spine_manifest", MOD)
    m = importlib.util.module_from_spec(spec)
    sys.modules["spine_manifest"] = m
    spec.loader.exec_module(m)
    return m


def test_manifest_has_shape_and_is_json_safe():
    m = _load()
    man = m.build_manifest()
    for k in ("meta", "service_count", "ok_count", "broken_count", "broken", "services"):
        assert k in man, k
    assert man["meta"]["mode"].startswith("report-only")
    json.dumps(man)                      # persisted as an artifact -> must round-trip
    # counts are internally consistent
    assert man["service_count"] == len(man["services"])
    assert man["broken_count"] == len([s for s in man["services"] if s["status"] != "ok"])


def test_every_manifest_module_is_actually_mounted():
    """The manifest is derived from the mounted set -- one source of truth with the
    armed ratchet (via app_surface_kl). If these diverge, the reference is lying."""
    m = _load()
    sys.path.insert(0, ROOT)
    import app_surface_kl
    kl = app_surface_kl.build_app_surface_kl()
    # the true source: every module that owns a MOUNTED route in the KL (this
    # includes app/ package routers, which the root-only mounts.mounted list omits)
    mounted_owners = {rec["module"] for rec in kl["routes"]["by_path"].values()
                      if rec.get("mounted")}
    man = m.build_manifest()
    for s in man["services"]:
        assert s["module"] in mounted_owners, s["module"]


def test_strict_exit_code_is_data_independent():
    m = _load()
    assert m.strict_exit_code({"broken_count": 0}) == 0
    assert m.strict_exit_code({"broken_count": 3}) == 1


def test_report_only_never_fails_even_when_broken():
    """The whole point of this PR: report-only exits 0 regardless of breakage."""
    m = _load()
    rc = m.main(["--quiet", "--emit", os.path.join(ROOT, "artifacts")])
    assert rc == 0


def test_declares_router_static_check():
    m = _load()
    # a real mounted router module declares a router...
    assert m._declares_router("verdict_breakdown_api")["has_router"] is True
    # ...a nonexistent module does not exist
    d = m._declares_router("no_such_service_module_xyz")
    assert d["exists"] is False and d["has_router"] is False


def test_generated_preview_is_valid_python_and_fail_loud():
    m = _load()
    man = m.build_manifest()
    preview = m.render_preview(man)
    ast.parse(preview)                   # the emitted spine file must parse
    assert "def include_spine" in preview
    assert "raise" in preview            # fail-loud is present in the generated code
    # the executable failure branch must RAISE, never swallow. (The header comment
    # legitimately *names* `except Exception: pass` as the anti-pattern it replaces,
    # so assert on the parsed code, not the raw text.)
    tree = ast.parse(preview)
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert raises, "generated include must contain a raise"


def test_preview_carries_no_gate_tripping_literals():
    """The generated file is emitted into a repo tree the ratchet + hollow gate
    scan. It must not contain the router-constructor / decorator literals, or a
    generated artifact would be mistaken for a hollow router (the scar that bit
    app_surface_kl twice)."""
    m = _load()
    preview = m.render_preview(m.build_manifest())
    at = "@"
    assert "FastAPI(" not in preview
    assert "APIRouter(" not in preview
    assert at + "router." not in preview
    assert at + "app." not in preview
