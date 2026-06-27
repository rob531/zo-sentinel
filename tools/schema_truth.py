"""schema_truth.py -- derive the CURRENT app DB schema straight from app/models.py
(the SQLAlchemy ORM IS the schema source of truth) so every directive grounds in the
schema in place TODAY, not a hand-maintained doc that drifts. AST-based: no imports,
no DB connection, no side effects -- safe to call on every generation cycle.

This is the fix for directives being generated 'with no regard for the DB schema': the
output is injected ahead of the static schema doc, and it names the EXACT tables, columns,
and the 7 risk axes -- and explicitly flags that published_overall_risk / trusted are
DERIVED by trust_gate(), not stored axes (the exact thing the fallback hallucinated).
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = ROOT / "app" / "models.py"

# Canonical risk axes (axis_name enum in mcp_llm_axis_scores). Kept here as the one
# place the generator reads them; matches verdict_breakdown_api.AXES.
AXES = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"]


def _type_of(call: ast.Call) -> str:
    if not call.args:
        return ""
    a0 = call.args[0]
    if isinstance(a0, ast.Name):
        return a0.id
    if isinstance(a0, ast.Attribute):
        return a0.attr
    if isinstance(a0, ast.Call):
        f = a0.func
        return getattr(f, "id", getattr(f, "attr", ""))
    return ""


def schema_markdown() -> str:
    if not MODELS.exists():
        return f"# LIVE SCHEMA: {MODELS} not found"
    try:
        tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    except Exception as e:
        return f"# LIVE SCHEMA: failed to parse app/models.py ({e})"

    out = ["# LIVE DB SCHEMA TRUTH (derived from app/models.py at generation time)",
           "# Build ONLY against these tables/columns. Do NOT invent columns or axes.", ""]
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        tablename, cols = None, []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if (isinstance(t, ast.Name) and t.id == "__tablename__"
                            and isinstance(stmt.value, ast.Constant)):
                        tablename = stmt.value.value
            name, val = None, None
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name, val = stmt.target.id, stmt.value
            elif (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                  and isinstance(stmt.targets[0], ast.Name)):
                name, val = stmt.targets[0].id, stmt.value
            if name and isinstance(val, ast.Call):
                fn = getattr(val.func, "id", getattr(val.func, "attr", ""))
                if fn in ("mapped_column", "Column"):
                    typ = _type_of(val)
                    cols.append(f"{name}({typ})" if typ else name)
        if tablename:
            out.append(f"## {node.name} -> table \"{tablename}\"")
            out.append("  columns: " + ", ".join(cols))
            out.append("")

    out.append("## risk axes -- axis_name enum in mcp_llm_axis_scores is EXACTLY these 7, no others:")
    out.append("  " + ", ".join(AXES))
    out.append("  DERIVED (NOT stored axes): published_overall_risk and 'trusted' come from "
               "trust_gating_override.trust_gate(url, name, {axis_name: label}).")
    return "\n".join(out)


if __name__ == "__main__":
    print(schema_markdown())
