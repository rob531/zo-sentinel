from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class ScanCountBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)


class RiskTierCount(BaseModel):
    risk_tier: str | None
    count: int = Field(ge=0)


class ScanCountCohortResponse(BaseModel):
    min_scans: int = Field(ge=0)
    cohorts: list[ScanCountBucket]
    tiers: list[RiskTierCount]
    never_scanned: int = Field(ge=0)


_REGISTRY_TABLE = McpServerRegistry.__tablename__
_BUCKET_ORDER = ("0", "1", "2-3", "4-9", "10+")


def get_scan_count_cohorts(
    session: Session,
    min_scans: int = 0,
) -> ScanCountCohortResponse:
    cohort_rows = session.execute(
        text(
            f"""
            SELECT
                CASE
                    WHEN scan_count IS NULL OR scan_count = 0 THEN '0'
                    WHEN scan_count = 1 THEN '1'
                    WHEN scan_count BETWEEN 2 AND 3 THEN '2-3'
                    WHEN scan_count BETWEEN 4 AND 9 THEN '4-9'
                    ELSE '10+'
                END AS bucket,
                COUNT(*) AS count
            FROM {_REGISTRY_TABLE}
            WHERE COALESCE(scan_count, 0) >= :min_scans
            GROUP BY
                CASE
                    WHEN scan_count IS NULL OR scan_count = 0 THEN '0'
                    WHEN scan_count = 1 THEN '1'
                    WHEN scan_count BETWEEN 2 AND 3 THEN '2-3'
                    WHEN scan_count BETWEEN 4 AND 9 THEN '4-9'
                    ELSE '10+'
                END
            """
        ),
        {"min_scans": min_scans},
    ).mappings()

    counts = {bucket: 0 for bucket in _BUCKET_ORDER}
    for row in cohort_rows:
        counts[row["bucket"]] = int(row["count"])

    tier_rows = session.execute(
        text(
            f"""
            SELECT risk_tier, COUNT(*) AS count
            FROM {_REGISTRY_TABLE}
            WHERE COALESCE(scan_count, 0) >= :min_scans
            GROUP BY risk_tier
            ORDER BY risk_tier
            """
        ),
        {"min_scans": min_scans},
    ).mappings()

    never_scanned = int(
        session.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM {_REGISTRY_TABLE}
                WHERE scan_count IS NULL OR scan_count = 0
                """
            )
        ).scalar_one()
    )

    return ScanCountCohortResponse(
        min_scans=min_scans,
        cohorts=[
            ScanCountBucket(bucket=bucket, count=counts[bucket])
            for bucket in _BUCKET_ORDER
        ],
        tiers=[
            RiskTierCount(risk_tier=row["risk_tier"], count=int(row["count"]))
            for row in tier_rows
        ],
        never_scanned=never_scanned,
    )


router = APIRouter()


@router.get(
    "/registry/scan-count-cohorts",
    response_model=ScanCountCohortResponse,
)
def scan_count_cohorts(
    min_scans: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ScanCountCohortResponse:
    return get_scan_count_cohorts(session=session, min_scans=min_scans)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    scan_count INTEGER,
                    last_scanned DATETIME,
                    last_seen DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    risk_tier,
                    verdict,
                    scan_count,
                    last_scanned,
                    last_seen
                ) VALUES (
                    :server_id,
                    :name,
                    :risk_tier,
                    :verdict,
                    :scan_count,
                    :last_scanned,
                    :last_seen
                )
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Server One",
                    "risk_tier": "critical",
                    "verdict": "malicious",
                    "scan_count": 0,
                    "last_scanned": None,
                    "last_seen": "2026-01-01T00:00:00Z",
                },
                {
                    "server_id": "server-2",
                    "name": "Server Two",
                    "risk_tier": "high",
                    "verdict": "suspicious",
                    "scan_count": 1,
                    "last_scanned": "2026-01-02T00:00:00Z",
                    "last_seen": "2026-01-02T00:00:00Z",
                },
                {
                    "server_id": "server-3",
                    "name": "Server Three",
                    "risk_tier": "medium",
                    "verdict": "clean",
                    "scan_count": 5,
                    "last_scanned": "2026-01-03T00:00:00Z",
                    "last_seen": "2026-01-03T00:00:00Z",
                },
                {
                    "server_id": "server-4",
                    "name": "Server Four",
                    "risk_tier": "critical",
                    "verdict": "malicious",
                    "scan_count": 20,
                    "last_scanned": "2026-01-04T00:00:00Z",
                    "last_seen": "2026-01-04T00:00:00Z",
                },
                {
                    "server_id": "server-5",
                    "name": "Server Five",
                    "risk_tier": "high",
                    "verdict": "suspicious",
                    "scan_count": 0,
                    "last_scanned": None,
                    "last_seen": "2026-01-05T00:00:00Z",
                },
                {
                    "server_id": "server-6",
                    "name": "Server Six",
                    "risk_tier": "medium",
                    "verdict": "clean",
                    "scan_count": 5,
                    "last_scanned": "2026-01-06T00:00:00Z",
                    "last_seen": "2026-01-06T00:00:00Z",
                },
            ],
        )

    def override_get_session() -> Iterator[Session]:
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-cohorts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["min_scans"] == 0
        assert sum(item["count"] for item in payload["cohorts"]) == 6
        assert payload["never_scanned"] == 2
        assert {item["risk_tier"] for item in payload["tiers"]} == {
            "critical",
            "high",
            "medium",
        }

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()