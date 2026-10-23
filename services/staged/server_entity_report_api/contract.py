from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session

try:
    from app.models import (
        McpLlmAxisScore,
        McpServerRegistry,
        VulnAdvisory,
        VulnLink,
    )
except ImportError:
    from app import models as _app_models

    McpLlmAxisScore = getattr(_app_models, "McpLlmAxisScore", None)
    McpServerRegistry = getattr(_app_models, "McpServerRegistry", None)
    VulnAdvisory = getattr(_app_models, "VulnAdvisory", None)
    VulnLink = getattr(_app_models, "VulnLink", None)


router = APIRouter(prefix="/api/servers", tags=["server-entity-report"])


def _identifier(value: str) -> str:
    """Quote a SQL identifier discovered from the database catalog."""

    return '"' + value.replace('"', '""') + '"'


def _columns(session: Session, table_name: str) -> Dict[str, Any]:
    try:
        bind = session.get_bind()
        return {column["name"]: column for column in inspect(bind).get_columns(table_name)}
    except Exception:
        return {}


def _first_present(columns: Dict[str, Any], candidates: Sequence[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            import json

            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _boolean(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _registry_query(
    session: Session, server_id: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    columns = _columns(session, "mcp_server_registry")
    if not columns:
        raise HTTPException(status_code=500, detail="server registry is unavailable")

    server_column = _first_present(columns, ("server_id", "id", "server"))
    if server_column is None:
        raise HTTPException(status_code=500, detail="server registry has no identifier")

    output_columns = {
        "name": _first_present(columns, ("name", "server_name")),
        "verdict": _first_present(columns, ("verdict",)),
        "risk_tier": _first_present(columns, ("risk_tier", "risk")),
        "trust_score": _first_present(columns, ("trust_score",)),
        "last_scanned": _first_present(columns, ("last_scanned", "last_scan")),
        "scan_count": _first_present(columns, ("scan_count",)),
    }

    selected = [
        f"{_identifier(server_column)} AS server_id",
    ]
    for output_name, source_column in output_columns.items():
        if source_column:
            selected.append(
                f"{_identifier(source_column)} AS {_identifier(output_name)}"
            )

    statement = text(
        "SELECT "
        + ", ".join(selected)
        + f" FROM mcp_server_registry WHERE {_identifier(server_column)} = :server_id"
    )
    row = session.execute(statement, {"server_id": server_id}).mappings().first()
    if row is None:
        return server_column, None
    return server_column, dict(row)


def _axis_rows(session: Session, server_id: str) -> List[Dict[str, Any]]:
    columns = _columns(session, "mcp_llm_axis_scores")
    if not columns:
        return []

    server_column = _first_present(
        columns, ("server_id", "server_registry_id", "registry_id", "server")
    )
    if server_column is None:
        return []

    axis_column = _first_present(columns, ("axis_name", "axis", "name"))
    label_column = _first_present(columns, ("label",))
    p_top_column = _first_present(columns, ("p_top",))
    p_critical_column = _first_present(columns, ("p_critical",))
    p_danger_column = _first_present(columns, ("p_danger",))
    probs_column = _first_present(columns, ("probs", "probabilities"))
    escalated_column = _first_present(columns, ("escalated",))

    selected = []
    for output_name, source_column in (
        ("axis_name", axis_column),
        ("label", label_column),
        ("p_top", p_top_column),
        ("p_critical", p_critical_column),
        ("p_danger", p_danger_column),
        ("probs", probs_column),
        ("escalated", escalated_column),
    ):
        if source_column:
            selected.append(
                f"{_identifier(source_column)} AS {_identifier(output_name)}"
            )

    if not selected:
        return []

    order_column = axis_column or "rowid"
    if order_column == "rowid" and "rowid" not in columns:
        order_column = axis_column or server_column

    statement = text(
        "SELECT "
        + ", ".join(selected)
        + f" FROM mcp_llm_axis_scores WHERE {_identifier(server_column)} = :server_id"
        + f" ORDER BY {_identifier(order_column)}"
    )
    rows = session.execute(statement, {"server_id": server_id}).mappings().all()

    result: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["axis_name"] = item.get("axis_name")
        item["label"] = item.get("label")
        item["p_top"] = item.get("p_top")
        item["p_critical"] = item.get("p_critical")
        item["p_danger"] = item.get("p_danger")
        item["probs"] = _json_value(item.get("probs"))
        item["escalated"] = _boolean(item.get("escalated"))
        result.append(item)
    return result


def _advisory_join_columns(
    link_columns: Dict[str, Any], advisory_columns: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    link_candidates = (
        "advisory_id",
        "vuln_advisory_id",
        "vulnerability_id",
        "vuln_id",
        "cve_id",
    )
    advisory_candidates = (
        "id",
        "advisory_id",
        "vulnerability_id",
        "vuln_id",
        "cve_id",
    )

    for link_column in link_candidates:
        if link_column not in link_columns:
            continue
        for advisory_column in advisory_candidates:
            if advisory_column in advisory_columns:
                return link_column, advisory_column

    return None


def _advisory_rows(session: Session, server_id: str) -> List[Dict[str, Any]]:
    link_columns = _columns(session, "vuln_links")
    advisory_columns = _columns(session, "vuln_advisories")
    if not link_columns or not advisory_columns:
        return []

    link_server_column = _first_present(
        link_columns, ("server_id", "server_registry_id", "registry_id", "server")
    )
    if link_server_column is None:
        return []

    join_columns = _advisory_join_columns(link_columns, advisory_columns)
    if join_columns is None:
        return []

    link_advisory_column, advisory_key_column = join_columns
    advisory_id_column = _first_present(
        advisory_columns, ("id", "advisory_id", "cve_id", "vuln_id")
    )
    feed_column = _first_present(advisory_columns, ("feed", "source"))
    severity_column = _first_present(advisory_columns, ("severity",))
    summary_column = _first_present(advisory_columns, ("summary", "description"))
    published_column = _first_present(
        advisory_columns, ("published_at", "published", "published_date")
    )

    selected = [
        f"a.{_identifier(advisory_id_column or advisory_key_column)} AS id"
    ]
    for output_name, source_column in (
        ("feed", feed_column),
        ("severity", severity_column),
        ("summary", summary_column),
        ("published_at", published_column),
    ):
        if source_column:
            selected.append(
                f"a.{_identifier(source_column)} AS {_identifier(output_name)}"
            )

    statement = text(
        "SELECT "
        + ", ".join(selected)
        + " FROM vuln_links AS l"
        + " JOIN vuln_advisories AS a"
        + f" ON a.{_identifier(advisory_key_column)} ="
        + f" l.{_identifier(link_advisory_column)}"
        + f" WHERE l.{_identifier(link_server_column)} = :server_id"
        + " ORDER BY a." + _identifier(advisory_id_column or advisory_key_column)
    )
    rows = session.execute(statement, {"server_id": server_id}).mappings().all()

    result: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        item = dict(row)
        item["id"] = item.get("id")
        item["feed"] = item.get("feed")
        item["severity"] = item.get("severity")
        item["summary"] = item.get("summary")
        item["published_at"] = item.get("published_at")
        key = (
            item["id"]
            if item["id"] is not None
            else (item["feed"], item["summary"])
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


@router.get("/{server_id}/report")
def get_server_entity_report(
    server_id: str, session: Session = Depends(get_session)
) -> Dict[str, Any]:
    _, registry = _registry_query(session, server_id)
    if registry is None:
        raise HTTPException(status_code=404, detail="server not found")

    axes = _axis_rows(session, server_id)
    advisories = _advisory_rows(session, server_id)

    severity_values = [str(item.get("severity") or "").lower() for item in advisories]
    cve_summary = {
        "total": len(advisories),
        "critical_count": sum(value == "critical" for value in severity_values),
        "high_count": sum(value == "high" for value in severity_values),
        "advisories": advisories,
    }

    return {
        "server_id": registry.get("server_id", server_id),
        "name": registry.get("name"),
        "verdict": registry.get("verdict"),
        "risk_tier": registry.get("risk_tier"),
        "trust_score": registry.get("trust_score"),
        "last_scanned": registry.get("last_scanned"),
        "scan_count": registry.get("scan_count"),
        "axes": axes,
        "cve_summary": cve_summary,
    }


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    verdict TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    last_scanned TEXT,
                    scan_count INTEGER,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    probs TEXT,
                    escalated INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    advisory_id TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id TEXT PRIMARY KEY,
                    feed TEXT,
                    severity TEXT,
                    summary TEXT,
                    published_at TEXT
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, verdict, risk_tier, trust_score, last_scanned, scan_count, meta)
                VALUES
                    (:server_id, :name, :verdict, :risk_tier, :trust_score, :last_scanned, :scan_count, :meta),
                    (:server_id_2, :name_2, :verdict_2, :risk_tier_2, :trust_score_2, :last_scanned_2, :scan_count_2, :meta_2)
                """
            ),
            {
                "server_id": "server-1",
                "name": "Primary Server",
                "verdict": "trusted",
                "risk_tier": "low",
                "trust_score": 0.97,
                "last_scanned": "2025-01-01T00:00:00",
                "scan_count": 4,
                "meta": "{}",
                "server_id_2": "server-2",
                "name_2": "Secondary Server",
                "verdict_2": "review",
                "risk_tier_2": "medium",
                "trust_score_2": 0.72,
                "last_scanned_2": "2025-01-02T00:00:00",
                "scan_count_2": 2,
                "meta_2": "{}",
            },
        )

        axis_names = (
            "confidentiality",
            "integrity",
            "availability",
            "authentication",
            "authorization",
            "supply_chain",
            "data_governance",
        )
        for server_id in ("server-1", "server-2"):
            for index, axis_name in enumerate(axis_names):
                connection.execute(
                    text(
                        """
                        INSERT INTO mcp_llm_axis_scores
                            (server_id, axis_name, label, p_top, p_critical, p_danger, probs, escalated)
                        VALUES
                            (:server_id, :axis_name, :label, :p_top, :p_critical, :p_danger, :probs, :escalated)
                        """
                    ),
                    {
                        "server_id": server_id,
                        "axis_name": axis_name,
                        "label": axis_name.replace("_", " ").title(),
                        "p_top": 0.8,
                        "p_critical": 0.05,
                        "p_danger": 0.15,
                        "probs": '{"safe": 0.8, "critical": 0.05, "danger": 0.15}',
                        "escalated": 0,
                    },
                )

        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, severity, summary, published_at)
                VALUES
                    ('CVE-2025-0001', 'nvd', 'critical', 'Test critical advisory', '2025-01-03T00:00:00'),
                    ('CVE-2025-0002', 'nvd', 'high', 'Test high advisory', '2025-01-04T00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (server_id, advisory_id)
                VALUES ('server-1', 'CVE-2025-0001'), ('server-2', 'CVE-2025-0002')
                """
            )
        )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    session_factory = Session(bind=engine, expire_on_commit=False)

    def override_get_session():
        yield session_factory

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/servers/server-1/report")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["server_id"] == "server-1"
            assert payload["name"] == "Primary Server"
            assert len(payload["axes"]) == 7
            assert payload["cve_summary"]["total"] == 1
            assert payload["cve_summary"]["critical_count"] == 1
            assert payload["cve_summary"]["high_count"] == 0
            assert any(
                advisory["id"] == "CVE-2025-0001"
                for advisory in payload["cve_summary"]["advisories"]
            )

            second_response = client.get("/api/servers/server-2/report")
            assert second_response.status_code == 200, second_response.text
            second_payload = second_response.json()
            assert second_payload["server_id"] == "server-2"
            assert len(second_payload["axes"]) == 7
            assert second_payload["cve_summary"]["total"] == 1
    finally:
        session_factory.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()