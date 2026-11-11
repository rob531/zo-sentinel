from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/axes", tags=["axes"])


class AxisThresholdOut(BaseModel):
    axis_name: str
    label: str
    scored_rows: int
    at_or_above_threshold: int
    escalated_rows: int
    critical_pct: float


class CriticalThresholdResponse(BaseModel):
    threshold: float
    axes: List[AxisThresholdOut]
    total_scored_rows: int


@router.get("/critical-threshold", response_model=CriticalThresholdResponse)
def get_critical_threshold(
    threshold: float = Query(default=0.5),
    axis_name: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> CriticalThresholdResponse:
    sql = """
        SELECT
            axis_name,
            label,
            COUNT(*) AS scored_rows,
            SUM(CASE WHEN p_critical >= :threshold THEN 1 ELSE 0 END) AS at_or_above,
            SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated_rows
        FROM mcp_llm_axis_scores
    """
    params = {"threshold": threshold}

    if axis_name is not None:
        sql += " WHERE axis_name = :axis_name"
        params["axis_name"] = axis_name

    sql += " GROUP BY axis_name, label ORDER BY axis_name"

    rows = session.execute(text(sql), params).mappings().all()

    axes = []
    total_scored_rows = 0
    for row in rows:
        scored_rows = int(row["scored_rows"])
        at_or_above = int(row["at_or_above"] or 0)
        escalated_rows = int(row["escalated_rows"] or 0)
        total_scored_rows += scored_rows
        axes.append(
            AxisThresholdOut(
                axis_name=row["axis_name"],
                label=row["label"],
                scored_rows=scored_rows,
                at_or_above_threshold=at_or_above,
                escalated_rows=escalated_rows,
                critical_pct=(at_or_above / scored_rows) if scored_rows else 0.0,
            )
        )

    return CriticalThresholdResponse(
        threshold=threshold,
        axes=axes,
        total_scored_rows=total_scored_rows,
    )


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    p_critical REAL,
                    escalated BOOLEAN
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, p_critical, escalated)
                VALUES
                    ('server-1', 'auth_strength', 'Authentication strength', 0.9, 1),
                    ('server-2', 'auth_strength', 'Authentication strength', 0.1, 0),
                    ('server-3', 'network_egress', 'Network egress', 0.2, 0)
                """
            )
        )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/critical-threshold")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 2

        auth_strength = next(
            axis for axis in payload["axes"] if axis["axis_name"] == "auth_strength"
        )
        assert auth_strength["scored_rows"] == 2
        assert auth_strength["at_or_above_threshold"] == 1
        assert auth_strength["escalated_rows"] == 1
        assert auth_strength["critical_pct"] == 0.5
        assert payload["total_scored_rows"] == 3

    print("PASS")


if __name__ == "__main__":
    run_self_test()