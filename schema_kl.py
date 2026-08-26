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

def lint_source(src: str, kl: dict, sql_catalog: set[str] | None = None) -> list[str]:
    """Return a list of schema-violation strings for `src` given the KL. Pure.

    `sql_catalog`, when supplied, is the set of table names that exist on ANY
    plane (see load_referent_catalog()). It switches on the SQL-string referent
    pass, which catches a phantom table named inside a SQL literal bound for the
    :8772 bus -- invisible to every AST check below. Left as None the function
    behaves exactly as before, so the pure self-test and any two-argument caller
    are unaffected."""
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

    if sql_catalog:
        # Union the KL's own table names (and the known-real extras above) into
        # the plane catalog. The catalog is built from the bus snapshot +
        # app/models + migrations; a table this module already asserts is real
        # -- mcp_discovery_candidates is one -- must not read as phantom just
        # because it is absent from a snapshot. For a BLOCKING check the safe
        # direction is a strict superset of what exists.
        violations.extend(
            lint_sql_referents(src, set(sql_catalog) | {t for t in tables if t}, tree=tree))

    seen, out = set(), []
    for v in violations:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out



# --------------------------------------------------------------------------- #
# SQL-string referent lint (the :8772 blind spot)
# --------------------------------------------------------------------------- #
#
# WHY THIS EXISTS
#   lint_source() above is an AST lint over PYTHON model/ORM usage against
#   app.models. A table named inside a SQL STRING LITERAL that is POSTed to the
#   write-service bus presents no Python schema surface at all -- no model
#   class, no constructor kwarg, no attribute access -- so every check above
#   sees nothing and passes it.
#
#   services/staged/circuit_breaker_status_api/contract.py used exactly that
#   route on 2026-08-25, AFTER the 2026-08-11 grounding ruling, to reference
#   `circuit_breaker_status` -- a table that exists on no plane:
#
#       requests.post("http://127.0.0.1:8772/query",
#                     json={"query": "SELECT ... FROM circuit_breaker_status"})
#
#   That is the only live route by which a new emission can still invent a
#   table. This closes it.
#
# SCOPE -- deliberately MODULE-scoped, not call-scoped
#   A module that addresses :8772 is a bus client, and its SQL literals are bus
#   payloads. Attributing each SQL string to the individual call that posts it
#   would be narrower, and it would also miss the commonest shape in this tree:
#
#       sql = "SELECT ... FROM t"          # built here
#       ws_query(sql)                      # posted somewhere else entirely
#
#   `ws_query` is not one helper -- it is copy-pasted per module (1000+ files
#   reference :8772). Call-scoped attribution would therefore have a large
#   false-NEGATIVE rate against precisely the shape this gate exists to catch,
#   which is the wrong direction for a check whose whole purpose is that the
#   last escape got through unseen.
#
# PRECISION
#   The extraction is reused verbatim from tools/referent_verify.py rather than
#   reimplemented, so there is exactly one SQL extractor in this repo and it is
#   the one already hardened against the false positives that were found the
#   hard way: prose docstrings beginning "Update ...", concatenated fragments,
#   f-string interpolations, CTE names, table aliases and code-created temp
#   tables. A second, divergent regex here would re-earn all of those bugs.
#
#   A table is satisfied if it exists on ANY plane (bus / app.models /
#   migrations) -- the same union rule referent_verify uses, and the weakest
#   form of the check. It catches the whole class B1 belongs to (referring to
#   something that is NOWHERE) at a false-positive rate this gate can defend.

_BUS_ADDR = ":8772"


def _referent_verify():
    """Import tools/referent_verify.py, or None. No side effects on import."""
    try:
        from tools import referent_verify as rv
        return rv
    except Exception:                                      # noqa: BLE001
        pass
    try:
        import importlib.util
        here = Path(__file__).resolve().parent / "tools" / "referent_verify.py"
        if not here.exists():
            return None
        spec = importlib.util.spec_from_file_location("_rv_for_schema_kl", here)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:                                      # noqa: BLE001
        return None


def load_referent_catalog() -> tuple[set[str] | None, str | None]:
    """Return (real table names across ALL planes, None) or (None, reason).

    THREE-STATE, and the third state is never folded into either of the others.
    (None, reason) means "could not evaluate" -- it is NOT an empty catalog.
    An empty catalog here would mark every table in every bus query as missing
    and block the entire fleet the moment the host snapshot went stale, which
    is how a gate earns itself an off switch.
    """
    rv = _referent_verify()
    if rv is None:
        return None, "tools/referent_verify.py is not importable"
    try:
        tables, _meta, reason = rv.load_catalog()
    except Exception as e:                                 # noqa: BLE001
        return None, f"catalog load raised {type(e).__name__}"
    if reason:
        return None, reason
    if not tables:
        return None, "catalog loaded but is empty"
    return set(tables), None


def module_addresses_bus(tree: ast.AST) -> bool:
    """True when this module talks to the write-service bus on :8772."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _BUS_ADDR in node.value:
                return True
        elif isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str) \
                        and _BUS_ADDR in v.value:
                    return True
    return False


def lint_sql_referents(src: str, catalog: set[str], tree: ast.AST | None = None) -> list[str]:
    """Return violations for tables named in bus-bound SQL that exist on no plane.

    PURE: `catalog` is passed in, never loaded here. Returns [] for a module
    that does not address the bus, and [] when the module has no SQL.
    """
    if not catalog:
        return []
    rv = _referent_verify()
    if rv is None:
        return []
    if tree is None:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return []
    if not module_addresses_bus(tree):
        return []

    real = {t.lower() for t in catalog}

    # Tables the module creates for itself (temp/staging) are real referents
    # for the length of the statement -- collected over the WHOLE module first,
    # exactly as referent_verify.scan_tree does, so a CREATE in one string
    # covers a SELECT in another.
    created: set[str] = set()
    stmts: list[tuple[str, int]] = list(rv._iter_sql_strings(tree))
    for sql, _ln in stmts:
        for cm in rv.CREATED_TABLE.finditer(sql):
            created.add(cm.group(1).lower())

    missing: dict[str, int] = {}
    for sql, lineno in stmts:
        tabs, _cols = rv.extract_refs(sql)
        for t in tabs:
            if t in real or t in created:
                continue
            missing.setdefault(t, lineno)

    out: list[str] = []
    for t in sorted(missing):
        out.append(
            f"phantom table '{t}' in a SQL string bound for the write-service bus "
            f"(:8772), line {missing[t]}: that table exists on NO plane -- it is not "
            f"in the bus catalog, not a __tablename__ in app/models.py, and in no "
            f"migration. Name a real table, or add a migration that creates it. "
            f"An AST lint cannot see this because a table named in a SQL STRING has "
            f"no Python schema surface -- which is how circuit_breaker_status got in.")
    return out


def lint_file(path: str, kl: dict, sql_catalog: set[str] | None = None) -> list[str]:
    return lint_source(Path(path).read_text(encoding="utf-8"), kl, sql_catalog)


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

    # --- SQL-string referent pass (the :8772 blind spot) --------------------
    # Reproduces services/staged/circuit_breaker_status_api/contract.py, the
    # emission that got through on 2026-08-25 AFTER the grounding ruling.
    catalog = {"service_health", "mcp_server_registry"}
    escaped = (
        "import requests\n"
        "def q():\n"
        "    return requests.post(\n"
        "        'http://127.0.0.1:8772/query',\n"
        "        json={'query': 'SELECT breaker_state FROM circuit_breaker_status LIMIT 1'},\n"
        "        timeout=5)\n")
    e = lint_source(escaped, kl, sql_catalog=catalog)
    assert any("circuit_breaker_status" in v for v in e), \
        f"the SQL-string blind spot is OPEN again: {e}"

    # the same shape naming a REAL table must pass
    fixed = escaped.replace("circuit_breaker_status", "service_health")
    f = lint_source(fixed, kl, sql_catalog=catalog)
    assert f == [], f"real table flagged: {f}"

    # a module that does NOT address the bus is out of scope
    offbus = escaped.replace("127.0.0.1:8772", "example.invalid")
    assert lint_source(offbus, kl, sql_catalog=catalog) == [], "non-bus module flagged"

    # THREE-STATE: no catalog means SKIPPED, never a silent pass turned into a
    # fleet-wide block. Both of these must be [] -- and the caller logs why.
    assert lint_source(escaped, kl, sql_catalog=None) == [], "None catalog blocked"
    assert lint_source(escaped, kl, sql_catalog=set()) == [], "empty catalog blocked"

    # a code-created temp table is a real referent, not a phantom
    tmp = (
        "import requests\n"
        "URL = 'http://127.0.0.1:8772/query'\n"
        "a = 'CREATE TEMP TABLE _stage AS SELECT 1'\n"
        "b = 'SELECT * FROM _stage'\n")
    assert lint_source(tmp, kl, sql_catalog=catalog) == [], "temp table flagged"

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
