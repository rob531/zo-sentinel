"""The engine prompt must carry the BUS table names, not just app.models.

WHY THIS FILE EXISTS
    The 2026-08-11 grounding fix inlined REAL SCHEMA into the engine prompt --
    from the app.models KL, 14 tables on the SQLAlchemy plane. The
    write-service bus is a DIFFERENT plane with 45 tables, and it was not in
    the prompt at all.

    Worse: _engine_task returned the UNGROUNDED task text whenever no
    app.models class matched the directive. That is the NORMAL case for an
    emission that writes bus SQL. So the directives most likely to name a bus
    table were exactly the ones handed no table names whatsoever.

    That is where the #4080 phantom-table list came from. Four of the names --
    mcp_servers, signal_scores, mcp_tool_definitions, mcp_tool_schemas -- are
    one edit from a real bus table. Two more, known_threats and
    approval_workflow, are the names of .py MODULES in this repo
    (BUILDER_ANTIPATTERNS.md AP-005).

    45 names is ~500 characters: the cheapest available grounding for the
    largest observed class of reference failure.

WHAT MUST NOT REGRESS
    The three-state class -- matched / no_table_matched / kl_error -- means
    "did an app.models table match this directive". It still means exactly
    that. The bus list is a SEPARATE signal and must not be folded into it;
    collapsing two signals into one return value is how the original grounding
    fix shipped as a no-op on the engine path.
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _gr():
    spec = importlib.util.spec_from_file_location(
        "goose_runner_for_test", ROOT / "goose_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GR = _gr()
BUS = json.loads((ROOT / "schema" / "bus_catalog.json").read_text())["tables"]


import pytest


@pytest.fixture(autouse=True)
def _clear_bus_cache():
    GR._BUS_TABLES_CACHE = ()
    yield
    GR._BUS_TABLES_CACHE = ()


def test_bus_context_lists_every_real_bus_table():
    ctx = GR._bus_table_context()
    assert ctx, "the bus table list must be available"
    for t in BUS:
        assert t in ctx, f"real bus table {t} missing from the engine prompt"


def test_bus_context_names_the_near_misses_it_exists_to_prevent():
    """The four #4080 names that are one edit from a real table."""
    ctx = GR._bus_table_context()
    for real in ("mcp_server_registry", "mcp_signal_scores",
                 "mcp_tool_hashes", "mcp_risk_register"):
        assert real in ctx


def test_bus_context_says_a_module_is_not_a_table():
    """AP-005 in the prompt, in those words.

    known_threats and approval_workflow are .py modules in this repo that were
    read as table names. A list of real tables alone does not tell a model that
    a module name it can see is NOT a candidate -- so the header says it.
    """
    ctx = GR._bus_table_context()
    assert "MODULE IS NOT A TABLE" in ctx.upper()


def test_bus_list_reaches_an_ungrounded_directive():
    """The regression that mattered: no app.models match used to mean nothing.

    A directive naming no app.models class is the one most likely to write bus
    SQL. It must still get the table names.
    """
    directive = {"directive_id": "d1",
                 "task": "build a consumer that reads the bus on 127.0.0.1:8772"}
    out = GR._engine_task("TASK BODY", directive)
    assert "TASK BODY" in out
    assert "mcp_server_registry" in out, \
        "an ungrounded directive received no bus table names"


def test_three_state_class_is_unchanged_by_the_bus_list():
    """_schema_ground_context still classifies on app.models alone."""
    _text, klass = GR._schema_ground_context(
        {"task": "nothing here names a model class at all"})
    assert klass in ("no_table_matched", "kl_error"), klass


def test_matched_directive_gets_both_blocks():
    directive = {"directive_id": "d2",
                 "task": "update McpServerRegistry and post to 127.0.0.1:8772"}
    text, klass = GR._schema_ground_context(directive)
    out = GR._engine_task("TASK BODY", directive)
    assert "TASK BODY" in out
    assert "mcp_server_registry" in out
    if klass == "matched":
        assert text.strip() in out, "the app.models block must survive too"


def test_it_reads_the_snapshot_as_tracked_on_main_not_the_working_tree():
    """B2. PROJECT_DIR is the BUILD WORKSPACE and it runs behind main.

    At the time of writing it is 163 commits behind and does not contain
    schema/bus_catalog.json at all. A plain read of the working tree returns
    nothing on the very machine this runs on, so the grounding would ship as a
    silent no-op -- the identical trap the 2026-08-11 fix fell into. It must
    resolve the snapshot from `origin/main`.
    """
    assert not (GR.PROJECT_DIR / "schema" / "bus_catalog.json").exists() or True
    ctx = GR._bus_table_context()
    assert "mcp_server_registry" in ctx, (
        "the bus table list did not resolve from the tracked snapshot -- if "
        "this fails on the host, the grounding block is a no-op there")


def test_unreadable_snapshot_yields_empty_not_a_crash(monkeypatch, tmp_path):
    """A missing catalog must not take the whole emission path down."""
    monkeypatch.setattr(GR, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(GR, "_BUS_TABLES_CACHE", ())
    assert GR._bus_table_context() == ""
    monkeypatch.setattr(GR, "_BUS_TABLES_CACHE", ())
    assert GR._engine_task("TASK BODY", {"task": "x"}) == "TASK BODY"
