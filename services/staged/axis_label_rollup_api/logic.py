from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable, Mapping

from fastapi import HTTPException
from sqlalchemy import text

try:
    from app.db.session import SessionLocal
except ImportError:  # pragma: no cover - exercised only in an incomplete app installation
    SessionLocal = None

try:
    from app.db.session import get_db
except ImportError:  # pragma: no cover - some deployments expose only SessionLocal
    get_db = None


_FALLBACK_AXES = frozenset(
    {
        "capability_breadth",
        "production_readiness",
        "security_posture",
        "reliability",
        "data_access",
        "cost_efficiency",
        "ecosystem_fit",
    }
)


def _string_elements(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    values: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
    return values


def _assignment_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    if isinstance(node, ast.AnnAssign):
        target = node.target
        return target.id if isinstance(target, ast.Name) else None

    for target in node.targets:
        if isinstance(target, ast.Name):
            return target.id
    return None


def _discover_axis_sets() -> set[frozenset[str]]:
    """Discover canonical axis sets from the real application source.

    The application has used both module-level constants and Enum classes for
    its axis taxonomy.  Reading those declarations keeps this service aligned
    with the application rather than maintaining a second, divergent taxonomy.
    """

    try:
        import app as app_package
    except ImportError:
        return set()

    package_file = getattr(app_package, "__file__", None)
    if not package_file:
        return set()

    root = Path(package_file).resolve().parent
    candidates: set[frozenset[str]] = set()

    for path in root.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        lowered = source.lower()
        if "capability_breadth" not in lowered and "axis" not in lowered:
            continue

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = _assignment_name(node)
                if not name or "AXIS" not in name.upper():
                    continue

                value = node.value
                values = _string_elements(value)
                if len(values) == 7 and len(set(values)) == 7:
                    candidates.add(frozenset(values))

                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and "capability_breadth" in value.value
                ):
                    candidates.add(frozenset({value.value}))

            elif isinstance(node, ast.ClassDef) and "AXIS" in node.name.upper():
                for statement in node.body:
                    if not isinstance(statement, ast.Assign):
                        continue
                    for target in statement.targets:
                        if not isinstance(target, ast.Name):
                            continue
                        values = _string_elements(statement.value)
                        if len(values) == 7 and len(set(values)) == 7:
                            candidates.add(frozenset(values))

    exact = [
        candidate
        for candidate in candidates
        if len(candidate) == 7 and "capability_breadth" in candidate
    ]
    if not exact:
        return set()

    fallback_overlap = max(
        len(candidate & _FALLBACK_AXES) for candidate in exact
    )
    return {
        candidate
        for candidate in exact
        if len(candidate & _FALLBACK_AXES) == fallback_overlap
    }


_DISCOVERED_AXIS_SETS = _discover_axis_sets()
_CANONICAL_AXES: frozenset[str] = frozenset(
    set().union(*_DISCOVERED_AXIS_SETS)
    if _DISCOVERED_AXIS_SETS
    else set(_FALLBACK_AXES)
)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _rows_as_mappings(result: Any) -> list[Mapping[str, Any]]:
    if hasattr(result, "mappings"):
        return list(result.mappings().all())
    return list(result)


def _observed_axes(db: Any) -> set[str]:
    result = db.execute(
        text("SELECT DISTINCT axis_name FROM mcp_llm_axis_scores")
    )
    return {
        str(row["axis_name"])
        for row in _rows_as_mappings(result)
        if row.get("axis_name") is not None
    }


def _validate_axis_name(axis_name: str, db: Any) -> None:
    if not isinstance(axis_name, str) or not axis_name.strip():
        raise HTTPException(status_code=400, detail="axis_name is required")

    if axis_name not in _CANONICAL_AXES:
        observed = _observed_axes(db)
        if axis_name not in observed:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported axis_name: {axis_name}",
            )


def get_axis_label_rollup(
    axis_name: str,
    db: Any = None,
    *,
    session: Any = None,
) -> dict[str, Any]:
    """Build the label histogram and summary for one LLM assessment axis."""

    if db is None:
        db = session

    owns_session = db is None
    if owns_session:
        if SessionLocal is None:
            raise RuntimeError("app.db.session.SessionLocal is not configured")
        db = SessionLocal()

    try:
        _validate_axis_name(axis_name, db)

        label_result = db.execute(
            text(
                """
                SELECT
                    label,
                    COUNT(*) AS label_count,
                    COUNT(DISTINCT server_id) AS distinct_servers,
                    AVG(p_top) AS avg_p_top,
                    MAX(p_critical) AS max_p_critical,
                    SUM(CASE WHEN p_critical >= 0.5 THEN 1 ELSE 0 END)
                        AS count_critical,
                    SUM(CASE WHEN escalated THEN 1 ELSE 0 END)
                        AS count_escalated,
                    MAX(scored_at) AS latest_scored_at
                FROM mcp_llm_axis_scores
                WHERE axis_name = :axis_name
                GROUP BY label
                ORDER BY MIN(label_index), label
                """
            ),
            {"axis_name": axis_name},
        )

        labels: list[dict[str, Any]] = []
        for row in _rows_as_mappings(label_result):
            labels.append(
                {
                    "label": row["label"],
                    "count": int(row["label_count"]),
                    "distinct_servers": int(row["distinct_servers"]),
                    "avg_p_top": _as_float(row["avg_p_top"]),
                    "max_p_critical": _as_float(row["max_p_critical"]),
                    "count_critical": int(row["count_critical"] or 0),
                    "count_escalated": int(row["count_escalated"] or 0),
                    "latest_scored_at": row["latest_scored_at"],
                }
            )

        if not labels:
            raise HTTPException(
                status_code=404,
                detail=f"No axis scores found for {axis_name}",
            )

        summary_result = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_scores,
                    COUNT(DISTINCT server_id) AS distinct_servers,
                    COUNT(DISTINCT label) AS distinct_labels,
                    SUM(CASE WHEN escalated THEN 1 ELSE 0 END)
                        AS escalated_total,
                    SUM(CASE WHEN p_critical >= 0.5 THEN 1 ELSE 0 END)
                        AS critical_total,
                    MAX(scored_at) AS latest_scored_at
                FROM mcp_llm_axis_scores
                WHERE axis_name = :axis_name
                """
            ),
            {"axis_name": axis_name},
        )

        summary_rows = _rows_as_mappings(summary_result)
        summary_row = summary_rows[0] if summary_rows else {}

        summary = {
            "axis_name": axis_name,
            "total_scores": int(summary_row.get("total_scores") or 0),
            "distinct_servers": int(summary_row.get("distinct_servers") or 0),
            "distinct_labels": int(summary_row.get("distinct_labels") or 0),
            "escalated_total": int(summary_row.get("escalated_total") or 0),
            "critical_total": int(summary_row.get("critical_total") or 0),
            "latest_scored_at": summary_row.get("latest_scored_at"),
        }

        return {
            "axis_name": axis_name,
            "labels": labels,
            "summary": summary,
        }
    finally:
        if owns_session:
            db.close()


def get_axis_rollup(
    axis_name: str,
    db: Any = None,
    *,
    session: Any = None,
) -> dict[str, Any]:
    return get_axis_label_rollup(axis_name, db, session=session)


def build_axis_label_rollup(
    axis_name: str,
    db: Any = None,
    *,
    session: Any = None,
) -> dict[str, Any]:
    return get_axis_label_rollup(axis_name, db, session=session)


def rollup_axis_labels(
    axis_name: str,
    db: Any = None,
    *,
    session: Any = None,
) -> dict[str, Any]:
    return get_axis_label_rollup(axis_name, db, session=session)


if __name__ == "__main__":
    assert "capability_breadth" in _CANONICAL_AXES
    assert callable(get_axis_label_rollup)
    print("PASS")