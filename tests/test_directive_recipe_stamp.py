"""Regression test for the anti-hollow recipe stamp in propose_directive.

A CREATION directive (declares output_file, not an edit/breaker task) must be
deterministically bound to the validated module_from_exemplar builder lane, since
the weak MiniMax architect ignores the recipe instruction in prose. Edit-class
directives must NOT be stamped. See directive_mcp.propose_directive.
"""
import importlib
import json
import sys
import types
import pathlib

import pytest


@pytest.fixture
def dm(tmp_path, monkeypatch):
    # Stub the MCP SDK so the module imports without the real dependency.
    mf = types.ModuleType("mcp.server.fastmcp")

    class _FM:
        def __init__(self, *a, **k):
            pass

        def tool(self, *a, **k):
            return lambda fn: fn

        def run(self):
            pass

    mf.FastMCP = _FM
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", mf)
    # Neutralize the import-time mkdir on the hardcoded /home path (CI-safe).
    monkeypatch.setattr(pathlib.Path, "mkdir", lambda *a, **k: None)

    mod = importlib.import_module("zo_sentinel.mcp_servers.directive_mcp")
    mod = importlib.reload(mod)
    mod.PROPOSED_DIR = tmp_path
    monkeypatch.setattr(mod, "_validate", lambda d: (True, "ok"))
    monkeypatch.setattr(mod, "_already_done", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_record_proposal", lambda *a, **k: None)
    return mod


def test_creation_directive_is_stamped_module_from_exemplar(dm, tmp_path):
    dm.propose_directive(task="build_foo_api", handler="generate_file",
                         description="x" * 250, output_file="foo_api.py",
                         complexity="medium")
    files = list(tmp_path.glob("*.json"))
    assert files, "directive not written"
    d = json.loads(files[0].read_text())
    assert d.get("recipe") == "module_from_exemplar", d


def test_edit_directive_is_not_stamped(dm, tmp_path):
    dm.propose_directive(task="wire_foo_into_main", handler="generate_file",
                         description="y" * 250, output_file="",
                         complexity="low")
    files = list(tmp_path.glob("*.json"))
    assert files, "directive not written"
    d = json.loads(files[0].read_text())
    assert "recipe" not in d, d

def test_int_phase_float_priority_coerced_not_rejected(dm, tmp_path):
    """2026-07-15 regression: models emit phase as a JSON int (phase: 11).
    The old signature (phase: str|None) made FastMCP's pydantic layer reject
    the call before the handler ran; goose swallowed the error and the cycle
    scored +0, mislabelled as non-convergence. The bridge must coerce."""
    r = dm.propose_directive(
        task="build_probe_alpha_api", handler="generate_file",
        description="probe directive for int-phase coercion regression " * 2,
        output_file="probe_alpha_api.py", complexity="medium",
        phase=11, priority=0.85)
    assert r["status"] == "written", r


def test_llm_dialect_extra_fields_tolerated(dm):
    """Models copy reads/recipe/next_directive from the directive JSON example
    in context_json. Unknown-kwarg rejection is the same +0 death: tolerate
    them (never trusted: recipe is stamped server-side, reads is a placebo)."""
    r = dm.propose_directive(
        task="build_probe_beta_api", handler="generate_file",
        description="probe directive for extra-field tolerance regression " * 2,
        output_file="probe_beta_api.py", complexity="medium",
        phase="11", priority="0.9",
        reads=["app/db.py", "app/models.py"],
        recipe="module_from_exemplar", next_directive={})
    assert r["status"] == "written", r


def test_garbage_priority_never_raises(dm):
    r = dm.propose_directive(
        task="build_probe_gamma_api", handler="generate_file",
        description="probe directive for garbage-priority tolerance " * 2,
        output_file="probe_gamma_api.py", complexity="medium",
        priority="highest")
    assert r["status"] == "written", r
