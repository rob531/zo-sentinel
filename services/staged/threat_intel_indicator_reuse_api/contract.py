from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class IndicatorReuseItem(BaseModel):
    indicator_type: str
    indicator_value: str
    pulse_id: Optional[str]
    use_count: int


class IndicatorReuseResponse(BaseModel):
    indicators: list[IndicatorReuseItem]
    total_indicators: int
    over_broad_count: int


router = APIRouter(prefix="/api")

_INDICATOR_USAGE_QUERY = f"""
SELECT
    CAST(a.indicator_type AS TEXT) AS indicator_type,
    CAST(a.indicator_value AS TEXT) AS indicator_value,
    MAX(CAST(r.pulse_id AS TEXT)) AS pulse_id,
    COUNT(DISTINCT a.server_id) AS use_count
FROM mcp_threat_associations AS a
LEFT JOIN {ThreatIntelRef.__tablename__} AS r
    ON r.indicator_type = a.indicator_type
   AND r.indicator_value = a.indicator_value
GROUP BY
    CAST(a.indicator_type AS TEXT),
    CAST(a.indicator_value AS TEXT)
ORDER BY
    use_count DESC,
    indicator_type ASC,
    indicator_value ASC
"""


@router.get(
    "/threat-intel/indicator-reuse",
    response_model=IndicatorReuseResponse,
)
def get_indicator_reuse(
    min_uses: int = 2,
    limit: int = 50,
    session=Depends(get_session),
) -> IndicatorReuseResponse:
    rows = session.execute(text(_INDICATOR_USAGE_QUERY)).mappings().all()
    indicators = [
        IndicatorReuseItem(
            indicator_type=row["indicator_type"],
            indicator_value=row["indicator_value"],
            pulse_id=row["pulse_id"],
            use_count=row["use_count"],
        )
        for row in rows
    ]
    over_broad = [item for item in indicators if item.use_count >= min_uses]

    return IndicatorReuseResponse(
        indicators=over_broad[: max(limit, 0)],
        total_indicators=len(indicators),
        over_broad_count=len(over_broad),
    )


def _run_self_test() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    is_aggregator INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    is_aggregator INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs
                    (id, indicator_type, indicator_value, pulse_id, pulse_name)
                VALUES
                    (1, 'ipv4', '203.0.113.10', 'pulse-broad', 'Broad pulse'),
                    (2, 'domain', 'single-use.example', 'pulse-single', 'Single-use pulse')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_threat_associations
                    (id, server_id, indicator_type, indicator_value)
                VALUES
                    (1, 'server-a', 'ipv4', '203.0.113.10'),
                    (2, 'server-b', 'ipv4', '203.0.113.10'),
                    (3, 'server-c', 'ipv4', '203.0.113.10'),
                    (4, 'server-d', 'domain', 'single-use.example')
                """
            )
        )

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/threat-intel/indicator-reuse")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["indicators"]) == 1, payload
        assert payload["indicators"][0]["use_count"] == 3, payload
        assert payload["indicators"][0]["indicator_value"] == "203.0.113.10", payload
        assert all(
            item["indicator_value"] != "single-use.example"
            for item in payload["indicators"]
        ), payload
        assert payload["total_indicators"] == 2, payload
        assert payload["over_broad_count"] == 1, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()