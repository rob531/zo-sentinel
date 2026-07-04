"""The fabricated-zero regression (2026-07-04): Glama returns empty tools[] for
servers it can't introspect; the ingestor must record UNKNOWN (None), not a
fabricated 0, and the backfill must null existing fabricated zeros. Pure tests,
no network / no DB."""
import importlib.util
import json
import os

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(relpath, name):
    p = os.path.join(_root, relpath)
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pag = _load("discovery_glama_paginator.py", "pag")
backfill = _load(os.path.join("tools", "backfill_glama_counts.py"), "glama_backfill")


def _meta(entry):
    return json.loads(pag.normalize_entry(entry)["metadata"])


# ---- ingestor: unknown stays unknown, real counts are recorded ----

def test_empty_tools_becomes_unknown_not_zero():
    md = _meta({"id": "abc", "name": "sap", "tools": [],
                "repository": {"url": "https://github.com/HUGO-Domon/sap-mcp-server"}})
    assert md["tool_count"] is None
    assert md["tool_count_verified"] is False


def test_missing_tools_key_becomes_unknown():
    md = _meta({"id": "abc", "name": "x",
                "repository": {"url": "https://github.com/o/r"}})
    assert md["tool_count"] is None
    assert md["tool_count_verified"] is False


def test_real_tools_are_recorded_and_verified():
    md = _meta({"id": "abc", "name": "x", "tools": [{"name": "a"}, {"name": "b"}],
                "environmentVariablesJsonSchema": {"properties": {"API_KEY": {}}},
                "repository": {"url": "https://github.com/o/r"}})
    assert md["tool_count"] == 2 and md["tool_count_verified"] is True
    assert md["env_var_count"] == 1 and md["env_var_count_verified"] is True


# ---- backfill: fabricated zeros nulled, verified rows untouched, idempotent ----

def test_backfill_nulls_fabricated_zero():
    meta = {"glama_id": "g1", "tool_count": 0, "env_var_count": 0}
    assert backfill.fix_meta(meta) is True
    assert meta["tool_count"] is None and meta["env_var_count"] is None
    assert meta["tool_count_verified"] is False
    assert "count_provenance" in meta


def test_backfill_leaves_verified_rows_alone():
    meta = {"glama_id": "g1", "tool_count": 5, "tool_count_verified": True,
            "env_var_count": 2, "env_var_count_verified": True}
    assert backfill.fix_meta(meta) is False
    assert meta["tool_count"] == 5


def test_backfill_ignores_non_glama_rows():
    assert backfill.fix_meta({"tool_count": 0}) is False


def test_backfill_idempotent():
    meta = {"glama_id": "g1", "tool_count": 0, "env_var_count": 0}
    backfill.fix_meta(meta)
    assert backfill.fix_meta(meta) is False   # second pass: nothing to change
