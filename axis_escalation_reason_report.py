from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from fastapi import Depends, FastAPI
from sqlalchemy import text

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


app = FastAPI()

_QUERY = """
WITH ranked_axis_scores AS (
    SELECT
        s.server_id,
        r.name AS server_name,
        s.axis_name,
        s.label,
        s.p_top,
        s.escalated_to,
        s.scored_at,
        ROW_NUMBER() OVER (
            PARTITION BY s.server_id, s.axis_name
            ORDER BY s.scored_at DESC, s.id DESC
        ) AS decision_rank
    FROM {score_table} AS s
    LEFT JOIN {registry_table} AS r ON r.id = s.server_id
    WHERE s.escalated IS TRUE
      AND s.scored_at >= :cutoff
)
SELECT
    server_id,
    server_name,
    axis_name,
    label,
    p_top,
    escalated_to,
    scored_at
FROM ranked_axis_scores
WHERE decision_rank = 1
ORDER BY p_top DESC NULLS LAST, scored_at DESC
LIMIT :top_n
""".format(
    score_table=McpLlmAxisScore.__tablename__,
    registry_table=McpServerRegistry.__tablename__,
)


def _query(session: Any, sql: str, params: dict[str, Any]) -> list[Any]:
    result = session.execute(text(sql).bindparams(**params))
    return list(result.mappings())


@contextmanager
def _session_scope() -> Iterator[Any]:
    provider = get_session()
    session = None
    entered_context = False
    try:
        if hasattr(provider, "__next__"):
            session = next(provider)
        elif hasattr(provider, "__enter__"):
            session = provider.__enter__()
            entered_context = True
        else:
            session = provider
        yield session
    finally:
        if entered_context and hasattr(provider, "__exit__"):
            provider.__exit__(None, None, None)
        elif session is not None and hasattr(session, "close"):
            session.close()
        if provider is not session and hasattr(provider, "close"):
            provider.close()


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(key, default)
    return getattr(row, key, default)


def _scored_at_string(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _markdown_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _build_report(session: Any, days: int, top_n: int) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")
    if top_n < 0:
        raise ValueError("top_n must be non-negative")

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)
    params = {"cutoff": cutoff, "top_n": top_n}
    query_rows = _query(session, _QUERY, params)

    rows: list[dict[str, Any]] = []
    for raw_row in query_rows[:top_n]:
        p_top = _row_value(raw_row, "p_top")
        rows.append(
            {
                "server_id": str(_row_value(raw_row, "server_id", "")),
                "server_name": (
                    None
                    if _row_value(raw_row, "server_name") is None
                    else str(_row_value(raw_row, "server_name"))
                ),
                "axis_name": str(_row_value(raw_row, "axis_name", "")),
                "label": str(_row_value(raw_row, "label", "")),
                "p_top": None if p_top is None else float(p_top),
                "escalated_to": (
                    None
                    if _row_value(raw_row, "escalated_to") is None
                    else str(_row_value(raw_row, "escalated_to"))
                ),
                "scored_at": _scored_at_string(_row_value(raw_row, "scored_at")),
            }
        )

    lines = [
        "# Axis Escalation Reason Report",
        "",
        f"- Window: {days} day(s)",
        f"- Generated at: {generated_at.isoformat()}",
        f"- Escalation decisions: {len(rows)}",
        "",
        "| Server ID | Server Name | Axis | Label | p_top | Escalated To | Scored At |",
        "|---|---|---|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {server_id} | {server_name} | {axis_name} | {label} | {p_top} | "
            "{escalated_to} | {scored_at} |".format(
                server_id=_markdown_cell(row["server_id"]),
                server_name=_markdown_cell(row["server_name"]),
                axis_name=_markdown_cell(row["axis_name"]),
                label=_markdown_cell(row["label"]),
                p_top=(
                    "—"
                    if row["p_top"] is None
                    else f"{row['p_top']:.6f}".rstrip("0").rstrip(".")
                ),
                escalated_to=_markdown_cell(row["escalated_to"]),
                scored_at=_markdown_cell(row["scored_at"]),
            )
        )

    if not rows:
        lines.extend(["", "No escalated axis decisions were found in this window."])

    return {
        "days": int(days),
        "generated_at": generated_at.isoformat(),
        "rows": rows,
        "markdown": "\n".join(lines),
    }


def build_report(days: int = 7, top_n: int = 20) -> dict[str, Any]:
    with _session_scope() as session:
        return _build_report(session, days, top_n)


@app.get("/axis-escalation-reason-report")
def axis_escalation_reason_report(
    days: int = 7,
    top_n: int = 20,
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    return _build_report(session, days, top_n)


if __name__ == "__main__":
    captured: dict[str, Any] = {}
    fake_rows = [
        {
            "server_id": "server-alpha",
            "server_name": "Alpha Server",
            "axis_name": "tool_invocation",
            "label": "destructive_tool_use",
            "p_top": 0.97,
            "escalated_to": "security-review",
            "scored_at": "2026-01-07T12:00:00+00:00",
        },
        {
            "server_id": "server-beta",
            "server_name": None,
            "axis_name": "data_access",
            "label": "sensitive_data_access",
            "p_top": 0.88,
            "escalated_to": "human-review",
            "scored_at": "2026-01-07T13:00:00+00:00",
        },
    ]

    original_query = _query

    def fake_query(session: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        captured["sql"] = sql
        captured["params"] = params
        return fake_rows

    try:
        globals()["_query"] = fake_query
        report = build_report(days=7, top_n=20)
    finally:
        globals()["_query"] = original_query

    assert len(report["rows"]) == 2
    assert "server-alpha" in report["markdown"]
    assert "server-beta" in report["markdown"]
    assert "mcp_llm_axis_scores" in captured["sql"]
    print("PASS")