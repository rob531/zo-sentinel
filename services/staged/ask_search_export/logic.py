from typing import Literal, Any
from fastapi import Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


def search_export(
    terms: str | None = None,
    perspective_id: int | None = None,
    format: Literal["json", "csv"] = "json",
    db: Session = Depends(get_session),
) -> dict[str, Any] | str:
    params: dict[str, Any] = {}
    conditions: list[str] = []

    if terms:
        conditions.append("aci.terms ILIKE :terms")
        params["terms"] = f"%{terms}%"

    if perspective_id is not None:
        conditions.append("msr.perspective_id = :perspective_id")
        params["perspective_id"] = perspective_id

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    sql = text(f"""
        SELECT
            msr.server_id,
            msr.name,
            msr.registry_source,
            msr.verdict,
            msr.risk_tier,
            aci.snippet
        FROM ask_corpus_index aci
        JOIN mcp_server_registry msr ON aci.server_id = msr.server_id
        {where_clause}
        ORDER BY msr.server_id
    """)

    rows = db.execute(sql, params).fetchall()

    if format == "csv":
        if not rows:
            return "server_id,name,registry_source,verdict,risk_tier,snippet\n"
        header = "server_id,name,registry_source,verdict,risk_tier,snippet"
        lines = [header]
        for r in rows:
            lines.append(
                f"{r.server_id},{_csv_escape(r.name)},{_csv_escape(r.registry_source)},"
                f"{_csv_escape(r.verdict or '')},{_csv_escape(r.risk_tier or '')},{_csv_escape(r.snippet or '')}"
            )
        return "\n".join(lines) + "\n"

    servers = [
        {
            "server_id": r.server_id,
            "name": r.name,
            "registry_source": r.registry_source,
            "verdict": r.verdict,
            "risk_tier": r.risk_tier,
            "snippet": r.snippet,
        }
        for r in rows
    ]
    return {"format": format, "servers": servers}


def _csv_escape(val: str | None) -> str:
    if val is None:
        return ""
    if any(c in val for c in (",", '"', "\n", "\r")):
        return '"' + val.replace('"', '""') + '"'
    return val


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from unittest.mock import MagicMock
    from fastapi import FastAPI

    mock_rows = [
        MagicMock(server_id="srv1", name="Alpha", registry_source="npm", verdict="safe", risk_tier="low", snippet="alpha snippet"),
        MagicMock(server_id="srv2", name="Beta", registry_source="github", verdict="unknown", risk_tier="medium", snippet="beta snippet"),
        MagicMock(server_id="srv3", name="Gamma", registry_source="npm", verdict="malicious", risk_tier="high", snippet="gamma snippet"),
    ]

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = mock_rows

    result_json = search_export(terms=None, perspective_id=None, format="json", db=mock_db)
    assert result_json["format"] == "json"
    assert len(result_json["servers"]) == 3
    assert result_json["servers"][0]["server_id"] == "srv1"

    result_csv = search_export(terms=None, perspective_id=None, format="csv", db=mock_db)
    assert result_csv.startswith("server_id,name,registry_source")
    assert "srv1" in result_csv
    assert len(result_csv.split("\n")) >= 4

    print("PASS")