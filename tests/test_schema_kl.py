"""Unit tests for the GraphifyKL schema PRM linter (pure -- no DB needed).

Verifies the deterministic checks that bounce the 2026-06-28 hallucination modes:
unknown constructor kwargs, unknown model attribute access, inline declarative_base.
"""
from schema_kl import lint_source

KL = {"models": {
    "McpLlmAxisScore": {"columns": ["id", "server_id", "axis_name", "label",
                                    "model_version", "scored_at", "escalated"],
                        "relationships": []},
    "McpServerRegistry": {"columns": ["server_id", "name", "url", "registry_source"],
                          "relationships": []},
}}


def test_clean_source_has_no_violations():
    src = ("from app.db import get_session, Base\n"
           "from app.models import McpLlmAxisScore, McpServerRegistry\n"
           "a = McpLlmAxisScore(id=1, server_id='s', axis_name='overall_risk',"
           " label='HIGH', model_version='v')\n"
           "b = McpServerRegistry(server_id='s', name='n', url='u', registry_source='r')\n"
           "q = McpLlmAxisScore.scored_at.desc()\n")
    assert lint_source(src, KL) == []


def test_unknown_constructor_kwarg_flagged():
    v = lint_source("from app.models import McpLlmAxisScore\n"
                    "x = McpLlmAxisScore(id=1, axis_label='x', score=0.5)\n", KL)
    assert any("axis_label" in s for s in v), v
    assert any("score" in s for s in v), v


def test_unknown_attribute_access_flagged():
    v = lint_source("from app.models import McpServerRegistry\n"
                    "q = McpServerRegistry.model_version\n", KL)
    assert any("model_version" in s for s in v), v


def test_inline_declarative_base_flagged():
    v = lint_source("from sqlalchemy.orm import declarative_base\n"
                    "Base = declarative_base()\n", KL)
    assert any("declarative_base" in s for s in v), v


def test_unrelated_code_not_flagged():
    # classes/attrs that are not known models must never be flagged
    assert lint_source("import os\nclass Foo:\n    pass\nx = os.path.join('a', 'b')\n", KL) == []


# --------------------------------------------------------------------------- #
# SQL-string referent pass -- the :8772 blind spot
# --------------------------------------------------------------------------- #
# lint_source's AST checks above see Python schema surface: model classes,
# constructor kwargs, attribute access. A table named inside a SQL STRING
# LITERAL posted to the write-service bus has none of that, so it passed every
# check -- which is how services/staged/circuit_breaker_status_api/contract.py
# referenced `circuit_breaker_status` (a table on no plane) on 2026-08-25,
# AFTER the 2026-08-11 grounding ruling.

CATALOG = {"service_health", "mcp_server_registry", "agent_runs"}

ESCAPED = (
    "import requests\n"
    "def q():\n"
    "    return requests.post(\n"
    "        'http://127.0.0.1:8772/query',\n"
    "        json={'query': 'SELECT breaker_state FROM circuit_breaker_status LIMIT 1'},\n"
    "        timeout=5)\n")


def test_phantom_table_in_bus_sql_is_flagged():
    v = lint_source(ESCAPED, KL, sql_catalog=CATALOG)
    assert any("circuit_breaker_status" in s for s in v), \
        f"the SQL-string blind spot is open again: {v}"


def test_real_table_in_bus_sql_passes():
    ok = ESCAPED.replace("circuit_breaker_status", "service_health")
    assert lint_source(ok, KL, sql_catalog=CATALOG) == []


def test_non_bus_module_is_out_of_scope():
    offbus = ESCAPED.replace("127.0.0.1:8772", "example.invalid")
    assert lint_source(offbus, KL, sql_catalog=CATALOG) == []


def test_no_catalog_is_skipped_not_blocked():
    """THREE-STATE. 'Could not evaluate' must not become a fleet-wide block.

    An empty catalog would mark every table in every bus query as phantom and
    stop every build the moment the host snapshot went stale.
    """
    assert lint_source(ESCAPED, KL, sql_catalog=None) == []
    assert lint_source(ESCAPED, KL, sql_catalog=set()) == []


def test_code_created_temp_table_is_not_phantom():
    src = ("import requests\n"
           "URL = 'http://127.0.0.1:8772/query'\n"
           "a = 'CREATE TEMP TABLE _stage AS SELECT 1'\n"
           "b = 'SELECT * FROM _stage'\n")
    assert lint_source(src, KL, sql_catalog=CATALOG) == []


def test_two_argument_callers_are_unaffected():
    """The SQL pass is opt-in; existing callers must not change behaviour."""
    assert lint_source(ESCAPED, KL) == []
