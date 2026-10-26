from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


BAND_ORDER = (
    "critical_ge_0.7",
    "danger_0.4_0.7",
    "elevated_0.2_0.4",
    "nominal_lt_0.2",
)


class CriticalProbabilityBand(BaseModel):
    band: str
    count: int
    server_ids: List[str]


class AxisCriticalProbabilityBandResponse(BaseModel):
    axis_name: str
    bands: List[CriticalProbabilityBand]
    total: int
    generated_at: str


router = APIRouter(prefix="/api", tags=["axis"])


def _band_for_probability(p_critical: Optional[float], p_danger: Optional[float]) -> str:
    values = [value for value in (p_critical, p_danger) if value is not None]
    probability = max(values) if values else 0.0

    if probability >= 0.7:
        return "critical_ge_0.7"
    if probability >= 0.4:
        return "danger_0.4_0.7"
    if probability >= 0.2:
        return "elevated_0.2_0.4"
    return "nominal_lt_0.2"


def compute_axis_critical_probability_band(
    session: Session,
    axis_name: str = "overall_risk",
    limit: int = 100,
) -> Dict[str, object]:
    statement = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpServerRegistry.risk_tier,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis_name)
        .order_by(McpLlmAxisScore.id.desc())
        .limit(limit)
    )

    rows = session.execute(statement).all()
    grouped: Dict[str, List[str]] = {band: [] for band in BAND_ORDER}

    for server_id, p_critical, p_danger, _risk_tier in rows:
        band = _band_for_probability(p_critical, p_danger)
        grouped[band].append(str(server_id))

    bands = [
        {
            "band": band,
            "count": len(grouped[band]),
            "server_ids": grouped[band],
        }
        for band in BAND_ORDER
        if grouped[band]
    ]

    return {
        "axis_name": axis_name,
        "bands": bands,
        "total": sum(int(band["count"]) for band in bands),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/axis/critical-band",
    response_model=AxisCriticalProbabilityBandResponse,
)
def get_axis_critical_probability_band(
    axis_name: str = Query(default="overall_risk"),
    limit: int = Query(default=100, ge=1, le=10000),
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    return compute_axis_critical_probability_band(
        session=session,
        axis_name=axis_name,
        limit=limit,
    )


app = FastAPI(title="axis_critical_probability_band_api")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                risk_tier VARCHAR
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR,
                axis_name VARCHAR,
                label VARCHAR,
                p_top FLOAT,
                p_critical FLOAT,
                p_danger FLOAT,
                escalated BOOLEAN,
                scored_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES
                ('server-1', 'Server One', 'critical'),
                ('server-2', 'Server Two', 'high'),
                ('server-3', 'Server Three', 'low'),
                ('server-4', 'Server Four', 'critical')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores
                (id, server_id, axis_name, label, p_top, p_critical, p_danger,
                 escalated, scored_at)
            VALUES
                (1, 'server-1', 'overall_risk', 'critical', 0.8, 0.8, 0.2, 0, '2024-01-01T00:00:00Z'),
                (2, 'server-2', 'overall_risk', 'danger', 0.5, 0.5, 0.3, 0, '2024-01-01T00:00:00Z'),
                (3, 'server-3', 'overall_risk', 'nominal', 0.1, 0.1, 0.1, 0, '2024-01-01T00:00:00Z'),
                (4, 'server-4', 'feature_risk', 'critical', 0.8, 0.8, 0.2, 0, '2024-01-01T00:00:00Z')
            """
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/axis/critical-band",
            params={"axis_name": "overall_risk", "limit": 100},
        )
        assert response.status_code == 200
        payload = response.json()

        response_fields = getattr(
            AxisCriticalProbabilityBandResponse,
            "model_fields",
            AxisCriticalProbabilityBandResponse.__fields__,
        )
        assert set(payload).issubset(set(response_fields))
        assert payload["axis_name"] == "overall_risk"
        assert len(payload["bands"]) == 3
        assert payload["total"] == 3

        critical_band = next(
            band for band in payload["bands"] if band["band"] == "critical_ge_0.7"
        )
        assert critical_band["count"] == 1
        assert critical_band["server_ids"] == ["server-1"]

        band_fields = getattr(
            CriticalProbabilityBand,
            "model_fields",
            CriticalProbabilityBand.__fields__,
        )
        for band in payload["bands"]:
            assert set(band).issubset(set(band_fields))

    test_session.close()
    test_engine.dispose()
    print("PASS")