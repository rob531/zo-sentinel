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
