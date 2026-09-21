#!/usr/bin/env python3
"""Emit the SCHEMA TRUTH: the exact importable names that exist in `app/`.

WHY (daily-chairman-review, 2026-08-09). The builder logged 250 RED self-tests in one
day. 55 of them -- the largest NAMED class -- fail because the module references a
symbol that does not exist: 'MCPAxisScores' and 'VulnAdvisories' are not in app.models,
'StaticPool' is not in app.db, and 'app.dependency_overrides' is not a submodule of the
`app` package. A prior review measured app/models.py defining 14 classes against 48
distinct model names referenced by builder output. Nothing in the builder's directive
ever handed it the real symbol table, so it invented one.

This publishes the table. It changes no schema and adds no gate. Deciding which of the
48 invented models SHOULD exist is a data-model decision and is deliberately not made
here.

AST-ONLY BY CONSTRUCTION. Importing app.models pulls in SQLAlchemy, app.settings and a
live engine at module scope; a read-only probe that connects to (or destroys) what it
measures is a known scar in this repo. This parses source and never executes it, so it
runs on a bare-stdlib CI with no deps and no DATABASE_URL.

Usage:
  python tools/emit_schema_truth.py            # rewrite docs/SCHEMA_TRUTH.md + .json
  python tools/emit_schema_truth.py --check    # exit 1 if a committed copy is stale
  python tools/emit_schema_truth.py --stdout   # print the markdown, write nothing
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_SRC = ROOT / "app" / "models.py"
DB_SRC = ROOT / "app" / "db.py"
MD_OUT = ROOT / "docs" / "SCHEMA_TRUTH.md"
JSON_OUT = ROOT / "docs" / "schema_truth.json"

# The names builder output invented most often on 2026-08-09, each with the real symbol
# that was meant. Counts are self-test REDs from that day's goose_runner log.
INVENTIONS = [
    ("package", "dependency_overrides", "app.dependency_overrides", 27,
     "`app` is the PACKAGE app/. The FastAPI instance is app.main:app -- write "
     "`from app.main import app`, then `app.dependency_overrides[get_session] = ...`"),
    ("db", "StaticPool", "from app.db import StaticPool", 15,
     "StaticPool is a SQLAlchemy pool class: `from sqlalchemy.pool import StaticPool`"),
    ("models", "MCPAxisScores", "from app.models import MCPAxisScores", 7,
     "the real class is `McpLlmAxisScore` (table mcp_llm_axis_scores)"),
    ("models", "VulnAdvisories", "from app.models import VulnAdvisories", 6,
     "the real class is `VulnAdvisory` (singular; table vuln_advisories)"),
]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_info(node: ast.ClassDef) -> dict:
    tablename = None
    columns: list[str] = []
    for st in node.body:
        if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            if not st.target.id.startswith("__"):
                columns.append(st.target.id)
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                if not isinstance(t, ast.Name):
                    continue
                if t.id == "__tablename__" and isinstance(st.value, ast.Constant):
                    tablename = st.value.value
                elif not t.id.startswith("__"):
                    columns.append(t.id)
    return {
        "class": node.name,
        "tablename": tablename,
        "bases": [ast.unparse(b) for b in node.bases],
        "columns": columns,
    }


def model_classes(tree: ast.Module) -> list[dict]:
    """Every top-level class in app/models.py, in source order."""
    return [_class_info(n) for n in tree.body if isinstance(n, ast.ClassDef)]


def public_module_names(tree: ast.Module) -> list[str]:
    """Every top-level name a `from <module> import X` could bind."""
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            names += [a.asname or a.name for a in node.names if a.name != "*"]
        elif isinstance(node, ast.Import):
            names += [a.asname or a.name.split(".")[0] for a in node.names]
    return sorted({n for n in names if not n.startswith("_")})


def still_absent(ns: str, name: str, model_names, db_names) -> bool:
    """Is this invented symbol STILL absent? Derived, never asserted -- if somebody
    legitimately adds the class later, the row drops out instead of the doc lying."""
    if ns == "models":
        return name not in model_names
    if ns == "db":
        return name not in db_names
    if (ROOT / "app" / (name + ".py")).exists():          # a real submodule
        return False
    init = ROOT / "app" / "__init__.py"
    if init.exists() and name in public_module_names(_parse(init)):
        return False
    return True


def build() -> tuple[str, dict]:
    models_tree = _parse(MODELS_SRC)
    models = model_classes(models_tree)
    models_public = public_module_names(models_tree)
    db_names = public_module_names(_parse(DB_SRC))
    model_names = [m["class"] for m in models]
    absent = [i for i in INVENTIONS if still_absent(i[0], i[1], model_names, db_names)]
    resolved = [i for i in INVENTIONS if i not in absent]
    payload = {
        "generated_by": "tools/emit_schema_truth.py",
        "sources": ["app/models.py", "app/db.py"],
        "app_models": {
            "count": len(models),
            "classes": [m["class"] for m in models],
            "detail": models,
            "public_names": models_public,
        },
        "app_db": {"public_names": db_names},
        "does_not_exist": [{"written": w, "reds_2026_08_09": c, "instead": f}
                           for _, _, w, c, f in absent],
        "resolved_since": [w for _, _, w, _, _ in resolved],
    }

    rows = "\n".join(
        "| `{c}` | `{t}` | {n} |".format(c=m["class"], t=m["tablename"] or "-",
                                         n=len(m["columns"]))
        for m in models)
    cols = "\n".join(
        "- `{c}` (`{t}`): {f}".format(c=m["class"], t=m["tablename"] or "-",
                                      f=", ".join("`%s`" % x for x in m["columns"]) or "-")
        for m in models)
    bad = "\n".join("| `{w}` | {c} | {f} |".format(w=w, c=c, f=f)
                    for _, _, w, c, f in absent) or "| (none still absent) | - | - |"
    md = f"""# SCHEMA TRUTH -- the exact names that exist in `app/`

GENERATED FILE -- do not hand-edit. Regenerate with `python tools/emit_schema_truth.py`.
`tests/test_schema_truth_current.py` (in the evaluator allowlist) re-runs the emitter
against the current `app/models.py` and `app/db.py` and fails if this file has drifted,
so it cannot silently go stale.

This file is DESCRIPTIVE. It reports what exists; it does not propose what should.

## Copy these import lines verbatim

```python
from app.db import get_session                  # the one session dependency
from app.models import <Model>                  # <Model> MUST be one of the {len(models)} below
```

Test-time dependency override -- the only correct spelling:

```python
from app.main import app                        # `app` here is the FastAPI INSTANCE
from sqlalchemy.pool import StaticPool          # NOT from app.db
app.dependency_overrides[get_session] = _override
```

## Does not exist (measured from 2026-08-09 self-test REDs)

| written by the builder | REDs that day | what to write instead |
| --- | --- | --- |
{bad}

If the model you want is not in the table below, it DOES NOT EXIST. Use the closest
real class, or say in the build notes that the directive needs a schema decision --
do not invent a class name.

### the dominant failure is SPELLING, not absence

Measured 2026-08-09 over the 2367 tracked .py files: **109 distinct names** are imported
from `app.models` that do not exist, spread over **370 modules**. The four commonest are
case/plural variants of classes that DO exist:

| written | files | real spelling |
| --- | --- | --- |
| `MCPServerRegistry` | 158 | `McpServerRegistry` |
| `MCPLLMAxisScores` | 112 | `McpLlmAxisScore` |
| `McpLlmAxisScores` | 33 | `McpLlmAxisScore` |
| `MCPScoreDisputes` | 31 | `McpScoreDispute` |

Python matches class names character for character: `MCP...` is never right, and no class
is plural. Copy the spelling out of the table below rather than reconstructing it.

## `app.models` -- all {len(models)} classes, exhaustive

| class | `__tablename__` | columns |
| --- | --- | --- |
{rows}

### columns per class

{cols}

## `app.models` -- all public top-level names, exhaustive

{", ".join("`%s`" % n for n in models_public)}

(That list includes third-party names re-exported by the module -- `Base` is genuinely
importable from `app.models`, but prefer importing SQLAlchemy names from SQLAlchemy.)

## `app.db` -- all public top-level names, exhaustive

{", ".join("`%s`" % n for n in db_names)}

Anything not in that list is not importable from `app.db`.
"""
    return md, payload


def _write(text: str, data: dict) -> None:
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text(text, encoding="utf-8", newline="\n")
    JSON_OUT.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n",
                        encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if a committed artifact is stale")
    ap.add_argument("--stdout", action="store_true", help="print markdown, write nothing")
    args = ap.parse_args(argv)

    md, payload = build()
    if args.stdout:
        sys.stdout.write(md)
        return 0
    if args.check:
        stale = []
        want = {MD_OUT: md, JSON_OUT: json.dumps(payload, indent=2, sort_keys=False) + "\n"}
        for path, expected in want.items():
            rel = path.relative_to(ROOT).as_posix()
            if not path.exists():
                stale.append(f"{rel}: MISSING (run tools/emit_schema_truth.py)")
            elif path.read_text(encoding="utf-8") != expected:
                stale.append(f"{rel}: STALE (does not match app/models.py + app/db.py)")
        if stale:
            for s in stale:
                print("FAIL " + s)
            return 1
        print(f"OK: {len(want)} artifact(s) match source; "
              f"app.models exports {payload['app_models']['count']} classes")
        return 0
    _write(md, payload)
    print(f"wrote {MD_OUT.relative_to(ROOT).as_posix()} and "
          f"{JSON_OUT.relative_to(ROOT).as_posix()} "
          f"({payload['app_models']['count']} model classes, "
          f"{len(payload['app_db']['public_names'])} app.db names)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())