from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api")


@router.get("/threat/indicators")
def get_indicators(
    indicator_type: str | None = Query(default=None),
    days: int = Query(default=30, ge=1),
    server_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    conditions: list[str] = []
    parameters: dict[str, Any] = {}

    if indicator_type is not None:
        conditions.append("ti.indicator_type = :indicator_type")
        parameters["indicator_type"] = indicator_type

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conditions.append("ti.pulse_created >= :cutoff")
    parameters["cutoff"] = cutoff

    if server_id is not None:
        conditions.append("ms.server_id = :server_id")
        parameters["server_id"] = server_id

    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)

    sql = text(
        """
        SELECT
            ti.id AS indicator_id,
            ti.indicator_type,
            ti.indicator_value,
            ti.pulse_id,
            ti.pulse_name,
            ti.pulse_created,
            ti.is_aggregator,
            ti.source,
            ti.source_url,
            ti.fetched_at,
            vl.server_id AS link_server_id,
            ms.name AS server_name,
            ms.risk_tier,
            vl.match_basis,
            vl.match_confidence,
            vl.advisory_id,
            va.severity AS advisory_severity,
            va.summary AS advisory_summary
        FROM threat_intel_refs AS ti
        LEFT JOIN vuln_links AS vl
            ON vl.match_value = ti.indicator_value
        LEFT JOIN vuln_advisories AS va
            ON va.id = vl.advisory_id
        LEFT JOIN mcp_server_registry AS ms
            ON ms.server_id = vl.server_id
        """
        + where_clause
        + """
        ORDER BY ti.pulse_created DESC, ti.id DESC, vl.id
        """
    )

    rows = session.execute(sql, parameters).mappings().all()

    indicators: list[dict[str, Any]] = []
    indicator_indexes: dict[Any, dict[str, Any]] = {}

    for row in rows:
        indicator_id = row["indicator_id"]
        indicator = indicator_indexes.get(indicator_id)

        if indicator is None:
            indicator = {
                "indicator_id": indicator_id,
                "indicator_type": row["indicator_type"],
                "indicator_value": row["indicator_value"],
                "pulse_id": row["pulse_id"],
                "pulse_name": row["pulse_name"],
                "pulse_created": row["pulse_created"],
                "is_aggregator": row["is_aggregator"],
                "source": row["source"],
                "source_url": row["source_url"],
                "fetched_at": row["fetched_at"],
                "links": [],
            }
            indicator_indexes[indicator_id] = indicator
            indicators.append(indicator)

        indicator["links"].append(
            {
                "server_id": row["link_server_id"],
                "server_name": row["server_name"],
                "risk_tier": row["risk_tier"],
                "match_basis": row["match_basis"],
                "match_confidence": row["match_confidence"],
                "advisory_id": row["advisory_id"],
                "advisory_severity": row["advisory_severity"],
                "advisory_summary": row["advisory_summary"],
            }
        )

    return {
        "days": days,
        "count": len(indicators),
        "indicators": indicators,
    }


get_threat_indicators = get_indicators


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type TEXT,
                    indicator_value TEXT,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN,
                    source TEXT,
                    source_url TEXT,
                    fetched_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id TEXT,
                    match_basis TEXT,
                    match_confidence REAL,
                    match_value TEXT,
                    linked_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    summary TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    source_url TEXT,
                    published_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs
                    (id, indicator_type, indicator_value, pulse_id, pulse_name,
                     pulse_created, is_aggregator, source, source_url, fetched_at)
                VALUES
                    (1, 'domain', 'example.test', 'pulse-1', 'Example pulse',
                     :now, 1, 'seed', 'https://example.test/source', :now),
                    (2, 'ip', '192.0.2.10', 'pulse-2', 'Second pulse',
                     :now, 0, 'seed', 'https://example.test/source-2', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, summary, severity, ecosystem, package,
                     source_url, published_at)
                VALUES
                    (10, 'nvd', 'Example advisory', 'high', 'python',
                     'example-package', 'https://example.test/advisory', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_confidence,
                     match_value, linked_at)
                VALUES
                    (100, 10, 'server-1', 'indicator_value', 0.95,
                     'example.test', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES ('server-1', 'Example server', 'high')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat/indicators", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2

        linked = next(
            item
            for item in payload["indicators"]
            if item["indicator_value"] == "example.test"
        )
        assert len(linked["links"]) == 1
        assert linked["links"][0]["server_id"] is not None
        assert linked["links"][0]["advisory_severity"] is not None

        filtered = client.get(
            "/api/threat/indicators",
            params={"days": 30, "indicator_type": "ip"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["count"] == 1

    print("PASS")