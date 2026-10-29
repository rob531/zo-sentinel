"""test_schema_header_orm.py -- the schema-grounding header must mandate the ORM,
not prime raw SQL.

The 2026-10-29 logic-build REDs persisted even after the engine inlined the exemplar:
the model kept emitting a raw `INSERT ... VALUES (?, ?, ...)` with a placeholder-count
bug against mcp_llm_axis_scores. Root: `_SCHEMA_HEADER` itself said "[required]=NOT NULL
with no default (a seed INSERT MUST set it)" -- priming raw SQL. The header reaches BOTH
build paths (_soa_schema_excerpt for the canary, _schema_ground_context for the engine),
so one edit fixes both: mandate ORM access + session.add(Model(...)) seeding and ban raw SQL.
"""
from __future__ import annotations

import goose_runner as gr


def test_header_mandates_orm_and_bans_raw_sql():
    h = gr._SCHEMA_HEADER
    assert "NEVER write raw SQL" in h
    assert "session.add(ModelName(" in h
    assert "ORM" in h


def test_header_no_longer_primes_raw_insert():
    # the old priming phrase that produced the raw-SQL seed INSERTs must be gone
    assert "a seed INSERT MUST" not in gr._SCHEMA_HEADER


def test_mandate_propagates_to_engine_grounding(monkeypatch):
    fake_kl = {"models": {"McpLlmAxisScore": {
        "table": "mcp_llm_axis_scores",
        "columns": ["id", "label", "p_top"],
        "column_meta": {}, "required_construct": ["label"]}}}
    monkeypatch.setattr(gr, "_schema_kl_cached", lambda: (fake_kl, None))
    text, klass = gr._schema_ground_context({"task": "build_mcp_llm_axis_scores_logic"})
    assert klass == "matched"
    assert "NEVER write raw SQL" in text          # the mandate reaches the engine prompt
    assert "mcp_llm_axis_scores" in text


def test_mandate_propagates_to_canary_excerpt(monkeypatch):
    fake_kl = {"models": {"McpLlmAxisScore": {
        "table": "mcp_llm_axis_scores",
        "columns": ["id", "label", "p_top"],
        "column_meta": {}, "required_construct": ["label"]}}}
    monkeypatch.setattr(gr, "_schema_kl_cached", lambda: (fake_kl, None))
    out = gr._soa_schema_excerpt({"task": "build_mcp_llm_axis_scores_logic"})
    assert out and "NEVER write raw SQL" in out    # same mandate on the canary path
