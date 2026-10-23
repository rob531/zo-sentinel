"""Negative-control test for FU-246 (CofC 2026-08-04).

WHY THIS FILE EXISTS. `service_dir_from_exemplar.yaml` declares `service_name`
and `service_spec` as `requirement: required`. `run_goose_task` passed only
`task_description`, so that recipe -- the SOA lane, selected for ~95% of
directives -- was invoked 1082 times and failed 1082 times in the
2026-08-03T09:09:26Z..2026-08-04T12:02Z window of
/home/workspace/logs/goose_runner.log. Nothing went red: the engine fallback
wrote the file anyway, and 162 tier1 successes on task_description-only recipes
kept the aggregate looking healthy.

HARNESS_DOCTRINE R4: an assertion never observed RED is not evidence. So this
test does NOT assert "the error message stopped appearing" -- that would pass if
the exception were merely swallowed somewhere else. It intercepts the ACTUAL
argv handed to subprocess.run and asserts the required params are present with
real values. test_negative_control_paramless_argv_would_fail proves the test can
distinguish the pre-fix behaviour, i.e. it has been seen RED on purpose.
"""
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RECIPE_DIR = REPO / "goose_recipes"
SOA_RECIPE_PATH = RECIPE_DIR / "service_dir_from_exemplar.yaml"


@pytest.fixture(scope="module")
def gr():
    pytest.importorskip("yaml", reason="goose_runner import deps")
    try:
        import goose_runner
    except Exception as exc:  # pragma: no cover
        pytest.skip("goose_runner not importable here: %s" % exc)
    return goose_runner


# --------------------------------------------------------------- the contract

def test_soa_recipe_still_declares_two_required_params():
    """If this fails the recipe changed its contract -- re-derive the fix."""
    assert SOA_RECIPE_PATH.exists(), SOA_RECIPE_PATH
    text = SOA_RECIPE_PATH.read_text(encoding="utf-8")
    assert "key: service_name" in text
    assert "key: service_spec" in text


def test_recipe_required_params_reads_the_recipes_own_contract(gr):
    req = gr.recipe_required_params(SOA_RECIPE_PATH)
    assert req == ["service_name", "service_spec"], req


def test_recipe_required_params_is_discriminating(gr):
    """A task_description-only recipe must NOT come back demanding two params --
    otherwise the parser is just echoing a hardcoded list again."""
    arch = RECIPE_DIR / "architect.yaml"
    if not arch.exists():
        pytest.skip("architect.yaml absent")
    assert gr.recipe_required_params(arch) == ["task_description"]


def test_recipe_required_params_on_a_paramless_file_is_empty(gr, tmp_path):
    p = tmp_path / "noparams.yaml"
    p.write_text("title: x\nprompt: |-\n  hi\n", encoding="utf-8")
    assert gr.recipe_required_params(p) == []


# ------------------------------------------------------- FU-209 path sanitising

@pytest.mark.parametrize("raw", [
    "build_<service>_thing", "a>b", "x<y>z", 'q"w', "p|q", "r?s", "t*u", "v:w",
])
def test_service_name_never_contains_a_windows_illegal_char(gr, raw):
    """FU-209: one such path makes EVERY checkout of main fail on the tower."""
    out = gr._sanitise_service_name(raw)
    for ch in '<>:"/\\|?*':
        assert ch not in out, (raw, out)


def test_service_name_derivation(gr):
    assert gr._soa_service_name(
        "build_risk_tier_dashboard_view_contract") == "risk_tier_dashboard_view"
    assert gr._soa_service_name(
        "scaffold_cve_analysis_dashboard_service_toml") == "cve_analysis_dashboard"
    assert gr._soa_service_name("") == "unnamed_service"


# ------------------------------------------- eligibility: no real table -> engine

def test_unresolvable_schema_makes_the_directive_ineligible(gr, monkeypatch):
    """The ruling: passing json.dumps(directive) raw as service_spec is NOT
    acceptable -- it hands invented columns the authority of a spec. No real
    table resolved => fall through to the engine, with a reason."""
    monkeypatch.setattr(gr, "_soa_schema_excerpt", lambda d: "")
    assert gr._soa_service_spec({"directive_id": "x"}, "content") is None


def test_resolved_schema_puts_real_columns_ahead_of_directive_intent(gr, monkeypatch):
    monkeypatch.setattr(
        gr, "_soa_schema_excerpt",
        lambda d: "REAL SCHEMA ...:\n  McpServerRegistry (table mcp_server_registry): server_id, url")
    spec = gr._soa_service_spec({"directive_id": "x"}, {"want": "id column"})
    assert spec is not None
    assert spec.index("REAL SCHEMA") < spec.index("UNVERIFIED INTENT")
    assert "server_id" in spec


def test_schema_excerpt_does_not_invent_an_id_column(gr):
    """The observed failure: generated code used McpServerRegistry.id when the
    PK is server_id and no `id` column exists (241 schema-prm BLOCKs on
    2026-08-04). If the excerpt resolves that model it must list server_id."""
    ex = gr._soa_schema_excerpt({"directive_id": "x",
                                 "description": "read mcp_server_registry"})
    if not ex:
        pytest.skip("schema KL unavailable in this environment")
    assert "server_id" in ex


# ============================ THE NEGATIVE CONTROL ============================
# Intercept the real argv. This is the only assertion that can tell "the params
# arrived" apart from "the error stopped being logged".

def _capture_argv(gr, monkeypatch, directive_id="build_registry_search_logic"):
    seen = {}

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        seen["argv"] = list(argv)
        return _Res()

    # goose_runner.PROJECT_DIR is the tower path (/home/workspace/zo_sentinel) and
    # does not resolve on CI/Windows; without pinning it, recipe_path.exists() is
    # False and the runner silently falls back to architect.yaml -- which requires
    # only task_description, so the test would pass on the WRONG recipe. That
    # substitution is itself an instance of the doctrine's one class, caught here.
    monkeypatch.setattr(gr, "PROJECT_DIR", REPO, raising=False)
    monkeypatch.setattr(gr.subprocess, "run", fake_run)
    monkeypatch.setattr(gr, "recipe_counter", lambda *a, **k: {})
    monkeypatch.setattr(gr, "_soa_budget_left", lambda: 99)
    monkeypatch.setattr(
        gr, "_soa_schema_excerpt",
        lambda d: "REAL SCHEMA:\n  McpServerRegistry (table mcp_server_registry): server_id, url")
    monkeypatch.setattr(gr, "SOA_GOOSE_MAX_PER_DAY", 8, raising=False)
    gr.run_goose_task(directive_id, {"task": "t"}, None,
                      recipe="service_dir_from_exemplar",
                      directive_obj={"directive_id": directive_id})
    return seen.get("argv") or []


def test_required_params_actually_arrive_in_the_argv(gr, monkeypatch):
    argv = _capture_argv(gr, monkeypatch)
    assert argv, "subprocess.run was never called"
    joined = " ".join(argv)
    assert "--recipe" in joined
    keys = [a.split("=", 1)[0] for a in argv if "=" in a and argv[argv.index(a) - 1] == "--params"]
    for required in ("service_name", "service_spec"):
        assert required in keys, (required, keys)
    for a in argv:
        if a.startswith("service_name="):
            val = a.split("=", 1)[1]
            assert val and val != "your_value"
            for ch in "<>":
                assert ch not in val
        if a.startswith("service_spec="):
            assert len(a.split("=", 1)[1]) > 20


def test_negative_control_paramless_argv_would_fail(gr, monkeypatch):
    """Proves this suite can go RED. Reconstruct the PRE-FIX argv and assert the
    check above rejects it. If this test ever passes vacuously, the assertion it
    guards is measuring nothing."""
    pre_fix_argv = ["goose", "run", "--recipe", str(SOA_RECIPE_PATH),
                    "--params", "task_description=%s" % json.dumps({"task": "t"})]
    keys = [a.split("=", 1)[0] for a in pre_fix_argv
            if "=" in a and pre_fix_argv[pre_fix_argv.index(a) - 1] == "--params"]
    assert "service_name" not in keys
    assert "service_spec" not in keys
    with pytest.raises(AssertionError):
        for required in ("service_name", "service_spec"):
            assert required in keys


def test_kill_switch_falls_through_to_engine(gr, monkeypatch):
    """SOA_GOOSE_MAX_PER_DAY=0 must route to the engine, NOT emit a paramless
    goose call that is certain to fail."""
    monkeypatch.setattr(gr, "SOA_GOOSE_MAX_PER_DAY", 0, raising=False)
    monkeypatch.setattr(gr, "recipe_counter", lambda *a, **k: {})
    out = gr.soa_params_or_none("build_x_logic", {"directive_id": "build_x_logic"}, "c")
    assert out is None


def test_daily_cap_falls_through_to_engine(gr, monkeypatch):
    monkeypatch.setattr(gr, "SOA_GOOSE_MAX_PER_DAY", 8, raising=False)
    monkeypatch.setattr(gr, "_soa_budget_left", lambda: 0)
    monkeypatch.setattr(gr, "recipe_counter", lambda *a, **k: {})
    assert gr.soa_params_or_none("build_x_logic", {"directive_id": "build_x_logic"}, "c") is None


def test_counter_distinguishes_zero_from_never_exercised(gr, tmp_path, monkeypatch):
    """Doctrine R3/R6: an attempts denominator is the whole point."""
    monkeypatch.setattr(gr, "_SOA_STATE_PATH", tmp_path / "s.json", raising=False)
    gr.recipe_counter("service_dir_from_exemplar", "attempt")
    row = gr.recipe_counter("service_dir_from_exemplar", "fail")
    assert row["attempt"] == 1 and row["fail"] == 1 and row["success"] == 0
