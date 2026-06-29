#!/usr/bin/env python3
"""schema_kl.py -- GraphifyKL schema knowledge layer + deterministic pre-build PRM.

This finishes the GraphifyKL story: the code graph (code_nodes/code_edges, built by
tools/index_graph.py) maps classes/functions/call-edges, but it does NOT carry the
DB-column detail a builder needs to avoid hallucinating the schema. This module adds
that missing layer.

Two halves:
  * build_schema_kl()  -- introspects the REAL app.models SQLAlchemy mappers into a
    schema knowledge layer {model: {columns, required_construct, relationships, ...}}
    and persists graphify-out/schema_kl.json (a first-class KL artifact, refreshed
    alongside the code graph).
  * lint_source()/lint_file() -- a PURE AST linter (needs only the KL dict, so it runs
    in CI without a DB) that bounces hallucinated schemas BEFORE a build is accepted.
    It is the deterministic PRM backstop the runtime self-test gate complements.

Catches the exact failure modes observed 2026-06-28 on the module_from_exemplar lane:
  - unknown constructor kwargs:    McpLlmAxisScore(axis_label=...) / (score=...)
  - unknown attribute access:      McpServerRegistry.model_version  (column lives on
                                   McpLlmAxisScore, not the registry)
  - inline declarative_base()/mock models in a lane that must import the real
    app.db Base + app.models classes.

The checks are HIGH-PRECISION (constructor kwargs, class-attribute access on the known
models, and a local declarative_base) so it is safe to run as a CI gate on every PR
without false-positiving on unrelated code.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

# Attributes legitimately accessible on a mapped class that are NOT columns, so the
# attribute-access check never false-positives on ORM machinery.
_SQLA_CLASS_ALLOW = {
    "metadata", "registry", "query", "classes", "__table__", "__tablename__",
    "__mapper__", "__table_args__", "__name__", "__init__", "__dict__", "__doc__",
    "__eq__", "__ne__", "c",
}

DEFAULT_KL_PATH = "graphify-out/schema_kl.json"


# --------------------------------------------------------------------------- #
# KL build (needs app.models importable)
# --------------------------------------------------------------------------- #

def build_schema_kl(models_module: str = "app.models") -> dict:
    """Introspect the real SQLAlchemy models into the schema KL dict.

    required_construct = columns that are NOT NULL, have no python/server default,
    and are not an autoincrement PK -- i.e. the kwargs a row MUST be built with
    (mirrors the exemplar's minimal seed; passing extra defaulted columns as None
    is what broke the 2026-06-28 builds)."""
    import importlib

    from sqlalchemy import inspect as sqla_inspect

    mod = importlib.import_module(models_module)
    base = importlib.import_module("app.db").Base

    kl: dict = {"models": {}, "source": models_module, "version": 1}
    for name in dir(mod):
        obj = getattr(mod, name)
        if not (isinstance(obj, type) and issubclass(obj, base) and obj is not base):
            continue
        try:
            mapper = sqla_inspect(obj)
        except Exception:
            continue
        columns: list[str] = []
        required: list[str] = []
        col_meta: dict = {}
        for ca in mapper.column_attrs:
            key = ca.key
            col = ca.columns[0]
            nullable = bool(getattr(col, "nullable", True))
            has_default = (col.default is not None) or (col.server_default is not None)
            autoinc = bool(col.primary_key) and getattr(col, "autoincrement", False) in (True, "auto")
            columns.append(key)
            col_meta[key] = {"nullable": nullable, "has_default": has_default,
                             "primary_key": bool(col.primary_key)}
            if (not nullable) and (not has_default) and (not autoinc):
                required.append(key)
        rels = [r.key for r in mapper.relationships]
        kl["models"][obj.__name__] = {
            "table": getattr(obj, "__tablename__", ""),
            "columns": sorted(columns),
            "required_construct": sorted(required),
            "relationships": sorted(rels),
            "column_meta": col_meta,
        }
    return kl


def write_schema_kl(path: str = DEFAULT_KL_PATH, kl: dict | None = None) -> Path:
    kl = kl if kl is not None else build_schema_kl()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kl, indent=2, sort_keys=True), encoding="utf-8")
    return p


def load_schema_kl(path: str = DEFAULT_KL_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# PRM linter (pure -- needs only the KL dict)
# --------------------------------------------------------------------------- #

def lint_source(src: str, kl: dict) -> list[str]:
    """Return a list of schema-violation strings for `src` given the KL. Pure."""
    models = kl.get("models", {})
    if not models:
        return []
    colset = {m: set(info.get("columns", [])) for m, info in models.items()}
    attrset = {m: set(info.get("columns", [])) | set(info.get("relationships", [])) | _SQLA_CLASS_ALLOW
               for m, info in models.items()}
    # Real table names (for the data-source guard below).
    tables = {info.get("table") for info in models.values() if info.get("table")} | {
        "mcp_signal_scores", "mesh_memory", "mcp_discovery_candidates", "mcp_submissions"}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        # local declarative_base() => inline/mock models (forbidden in the exemplar lane)
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if fname == "declarative_base":
                violations.append(
                    "inline declarative_base() -- do not define a local Base/models; "
                    "import the real app.db Base + app.models classes (mirror the exemplar)")
            # constructor kwargs on a known model: Model(unknown_kwarg=...)
            if isinstance(fn, ast.Name) and fn.id in colset:
                cols = colset[fn.id]
                for kw in node.keywords:
                    if kw.arg and kw.arg not in cols:
                        violations.append(
                            f"{fn.id}(...): unknown column kwarg '{kw.arg}' "
                            f"(valid columns: {sorted(cols)})")
        # class-attribute access on a known model: Model.unknown_attr
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mname = node.value.id
            if mname in attrset:
                attr = node.attr
                if attr not in attrset[mname] and not attr.startswith("__"):
                    violations.append(
                        f"{mname}.{attr}: unknown attribute (not a column of {mname}; "
                        f"valid columns: {sorted(colset[mname])})")
        # data-source hallucination: reading a KNOWN DB TABLE from a CSV/file instead
        # of the database. HIGH-PRECISION: only when a string literal's basename stem
        # is exactly a real table name, so legit exports (registry_export.csv, etc.)
        # do not trip. Scores/registry live in the DB -- read via write_service /query.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            _low = node.value.lower()
            if _low.endswith(".csv"):
                _stem = _low.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:-4]
                if _stem in tables:
                    violations.append(
                        f"data-source hallucination: '{node.value}' -- '{_stem}' is a "
                        f"DATABASE table, not a CSV. Read it from the DB via the write_service "
                        f"/query endpoint (POST http://127.0.0.1:8772/query, e.g. "
                        f"ws_query(\"SELECT ... FROM {_stem} ...\")), never a local CSV file.")

    seen, out = set(), []
    for v in violations:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def lint_file(path: str, kl: dict) -> list[str]:
    return lint_source(Path(path).read_text(encoding="utf-8"), kl)


# --------------------------------------------------------------------------- #
# CLI + self-test
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    """Pure self-test with a synthetic KL (no app import needed)."""
    kl = {"models": {
        "McpLlmAxisScore": {"columns": ["id", "server_id", "axis_name", "label",
                                        "model_version", "scored_at", "escalated"],
                            "relationships": []},
        "McpServerRegistry": {"columns": ["server_id", "name", "url", "registry_source"],
                              "relationships": []},
    }}
    good = (
        "from app.db import get_session, Base\n"
        "from app.models import McpLlmAxisScore, McpServerRegistry\n"
        "x = McpLlmAxisScore(id=1, server_id='s', axis_name='overall_risk', label='HIGH', model_version='v')\n"
        "y = McpServerRegistry(server_id='s', name='n', url='u', registry_source='r')\n"
        "q = McpLlmAxisScore.scored_at.desc()\n")
    bad = (
        "from sqlalchemy.orm import declarative_base\n"
        "Base = declarative_base()\n"
        "a = McpLlmAxisScore(id=1, server_id='s', axis_label='overall_risk', score=0.5)\n"
        "z = McpServerRegistry.model_version\n")
    g = lint_source(good, kl)
    b = lint_source(bad, kl)
    assert g == [], f"clean source flagged: {g}"
    assert any("axis_label" in v for v in b), b
    assert any("score" in v for v in b), b
    assert any("model_version" in v for v in b), b
    assert any("declarative_base" in v for v in b), b
    print("schema_kl self-test PASS")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GraphifyKL schema layer + PRM linter")
    ap.add_argument("--write", action="store_true", help="build the KL and write the json artifact")
    ap.add_argument("--out", default=DEFAULT_KL_PATH, help="KL json path")
    ap.add_argument("--lint", metavar="FILE", help="lint a file against the KL")
    ap.add_argument("--kl", default=DEFAULT_KL_PATH, help="KL json path for --lint")
    ap.add_argument("--selftest", action="store_true", help="run the pure self-test")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.write:
        p = write_schema_kl(a.out)
        kl = load_schema_kl(a.out)
        print(f"wrote {p} ({len(kl.get('models', {}))} models)")
        return 0
    if a.lint:
        kl = load_schema_kl(a.kl)
        vio = lint_file(a.lint, kl)
        if vio:
            print(f"SCHEMA VIOLATIONS in {a.lint}:")
            for v in vio:
                print(f"  - {v}")
            return 1
        print(f"OK: {a.lint} has no schema violations")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
