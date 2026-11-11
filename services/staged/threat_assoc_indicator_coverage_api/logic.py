from __future__ import annotations

from typing import Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class IndicatorTypeCoverage(BaseModel):
    indicator_type: str
    association_count: int
    distinct_servers: int
    distinct_indicators: int
    by_severity: Dict[str, int] = Field(default_factory=dict)


class IndicatorCoverageTotals(BaseModel):
    associations: int
    distinct_servers: int


class IndicatorCoverageResponse(BaseModel):
    indicator_types: List[IndicatorTypeCoverage] = Field(default_factory=list)
    totals: IndicatorCoverageTotals


_BUCKETS_QUERY = text(
    """
    WITH association_rows AS (
        SELECT
            a.indicator_type,
            a.severity,
            a.server_id,
            a.threat_id,
            s.name AS server_name,
            s.risk_tier
        FROM mcp_threat_associations AS a
        LEFT JOIN mcp_server_registry AS s
            ON s.server_id = a.server_id
    ),
    indicator_counts AS (
        SELECT
            indicator_type,
            COUNT(*) AS association_count,
            COUNT(DISTINCT server_id) AS distinct_servers,
            COUNT(DISTINCT threat_id) AS distinct_indicators
        FROM association_rows
        GROUP BY indicator_type
    ),
    severity_counts AS (
        SELECT
            indicator_type,
            severity,
            COUNT(*) AS severity_count
        FROM association_rows
        GROUP BY indicator_type, severity
    )
    SELECT
        i.indicator_type,
        i.association_count,
        i.distinct_servers,
        i.distinct_indicators,
        s.severity,
        s.severity_count
    FROM indicator_counts AS i
    LEFT JOIN severity_counts AS s
        ON s.indicator_type IS i.indicator_type
    ORDER BY i.indicator_type, s.severity
    """
)

_TOTALS_QUERY = text(
    """
    SELECT
        COUNT(*) AS associations,
        COUNT(DISTINCT a.server_id) AS distinct_servers
    FROM mcp_threat_associations AS a
    """
)


def get_indicator_coverage(session: Session) -> IndicatorCoverageResponse:
    buckets: Dict[str, IndicatorTypeCoverage] = {}

    for row in session.execute(_BUCKETS_QUERY).mappings().all():
        indicator_type = row["indicator_type"] or "unknown"
        severity = row["severity"] or "unknown"

        if indicator_type not in buckets:
            buckets[indicator_type] = IndicatorTypeCoverage(
                indicator_type=indicator_type,
                association_count=0,
                distinct_servers=0,
                distinct_indicators=0,
                by_severity={},
            )

        bucket = buckets[indicator_type]
        bucket.association_count = int(row["association_count"])
        bucket.distinct_servers = int(row["distinct_servers"])
        bucket.distinct_indicators = int(row["distinct_indicators"])
        bucket.by_severity[severity] = int(row["severity_count"])

    totals_row = session.execute(_TOTALS_QUERY).mappings().one()

    return IndicatorCoverageResponse(
        indicator_types=list(buckets.values()),
        totals=IndicatorCoverageTotals(
            associations=int(totals_row["associations"]),
            distinct_servers=int(totals_row["distinct_servers"]),
        ),
    )


# Compatibility aliases for callers that use a more explicit service name.
get_indicator_coverage_data = get_indicator_coverage
build_indicator_coverage = get_indicator_coverage


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
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        test_session.execute(
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
        test_session.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    server_id TEXT,
                    threat_id TEXT,
                    severity TEXT,
                    confidence REAL,
                    detected_at TEXT,
                    source TEXT,
                    indicator_type TEXT
                )
                """
            )
        )
        test_session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES
                    ('server-1', 'Server One', 'high'),
                    ('server-2', 'Server Two', 'medium')
                """
            )
        )
        test_session.execute(
            text(
                """
                INSERT INTO mcp_threat_associations
                    (server_id, threat_id, severity, confidence, detected_at, source, indicator_type)
                VALUES
                    ('server-1', 'threat-1', 'high', 0.9, '2024-01-01', 'test', 'malware'),
                    ('server-1', 'threat-2', 'high', 0.8, '2024-01-02', 'test', 'malware'),
                    ('server-2', 'threat-3', 'medium', 0.7, '2024-01-03', 'test', 'malware'),
                    ('server-1', 'threat-4', 'critical', 0.95, '2024-01-04', 'test', 'exploit'),
                    ('server-2', 'threat-5', 'critical', 0.85, '2024-01-05', 'test', 'exploit'),
                    ('server-2', 'threat-6', 'medium', 0.75, '2024-01-06', 'test', 'phishing')
                """
            )
        )
        test_session.commit()

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = lambda: test_session

        @test_app.get("/api/threat/associations/indicator-coverage")
        def indicator_coverage_endpoint(session: Session = Depends(get_session)):
            return get_indicator_coverage(session)

        with TestClient(test_app) as client:
            response = client.get("/api/threat/associations/indicator-coverage")
            assert response.status_code == 200
            payload = response.json()
            assert payload["totals"]["associations"] == 6
            malware = next(
                item
                for item in payload["indicator_types"]
                if item["indicator_type"] == "malware"
            )
            assert malware["association_count"] == 3

    print("PASS")