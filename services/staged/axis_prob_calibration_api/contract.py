from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class CalibrationVersion(BaseModel):
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    rows: int


class AxisCalibration(BaseModel):
    axis_name: AxisName
    rows: int
    avg_p_top: Optional[float] = None
    min_p_top: Optional[float] = None
    max_p_top: Optional[float] = None
    confident_share: float
    uncertain_share: float
    escalated_count: int
    escalated_rate: float
    avg_p_critical: Optional[float] = None
    avg_p_danger: Optional[float] = None
    versions: list[CalibrationVersion] = Field(default_factory=list)


class CalibrationResponse(BaseModel):
    generated_at: datetime
    axes: list[AxisCalibration]


router = APIRouter()


@router.get(
    "/scoring/calibration",
    response_model=CalibrationResponse,
    summary="Report LLM scoring calibration by risk axis",
)
def get_axis_calibration(
    axis: Optional[AxisName] = Query(default=None),
    session: Session = Depends(get_session),
) -> CalibrationResponse:
    axis_filter = ""
    parameters: dict[str, object] = {}

    if axis is not None:
        axis_filter = "WHERE axis_name = :axis_name"
        parameters["axis_name"] = axis

    aggregate_sql = text(
        f"""
        SELECT
            axis_name,
            COUNT(*) AS rows,
            AVG(p_top) AS avg_p_top,
            MIN(p_top) AS min_p_top,
            MAX(p_top) AS max_p_top,
            SUM(CASE WHEN p_top >= 0.8 THEN 1 ELSE 0 END) AS confident_rows,
            SUM(CASE WHEN p_top <= 0.5 THEN 1 ELSE 0 END) AS uncertain_rows,
            SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated_count,
            AVG(p_critical) AS avg_p_critical,
            AVG(p_danger) AS avg_p_danger
        FROM mcp_llm_axis_scores
        {axis_filter}
        GROUP BY axis_name
        ORDER BY axis_name
        """
    )

    version_sql = text(
        f"""
        SELECT
            axis_name,
            decision_rule_version,
            model_version,
            COUNT(*) AS rows
        FROM mcp_llm_axis_scores
        {axis_filter}
        GROUP BY axis_name, decision_rule_version, model_version
        ORDER BY axis_name, decision_rule_version, model_version
        """
    )

    aggregate_rows = session.execute(aggregate_sql, parameters).mappings().all()
    version_rows = session.execute(version_sql, parameters).mappings().all()

    versions_by_axis: dict[str, list[CalibrationVersion]] = {}
    for row in version_rows:
        versions_by_axis.setdefault(row["axis_name"], []).append(
            CalibrationVersion(
                decision_rule_version=row["decision_rule_version"],
                model_version=row["model_version"],
                rows=int(row["rows"]),
            )
        )

    axes: list[AxisCalibration] = []
    for row in aggregate_rows:
        rows = int(row["rows"])
        axes.append(
            AxisCalibration(
                axis_name=row["axis_name"],
                rows=rows,
                avg_p_top=float(row["avg_p_top"]) if row["avg_p_top"] is not None else None,
                min_p_top=float(row["min_p_top"]) if row["min_p_top"] is not None else None,
                max_p_top=float(row["max_p_top"]) if row["max_p_top"] is not None else None,
                confident_share=(
                    float(row["confident_rows"]) / rows if rows else 0.0
                ),
                uncertain_share=(
                    float(row["uncertain_rows"]) / rows if rows else 0.0
                ),
                escalated_count=int(row["escalated_count"] or 0),
                escalated_rate=(
                    float(row["escalated_count"] or 0) / rows if rows else 0.0
                ),
                avg_p_critical=(
                    float(row["avg_p_critical"])
                    if row["avg_p_critical"] is not None
                    else None
                ),
                avg_p_danger=(
                    float(row["avg_p_danger"])
                    if row["avg_p_danger"] is not None
                    else None
                ),
                versions=versions_by_axis.get(row["axis_name"], []),
            )
        )

    return CalibrationResponse(
        generated_at=datetime.now(timezone.utc),
        axes=axes,
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR,
                    label_index INTEGER,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (axis_name, label, label_index, p_top, p_critical, p_danger,
                     escalated, escalated_to, decision_rule_version, model_version,
                     scored_at)
                VALUES
                    ('overall_risk', 'critical', 0, 0.95, 0.90, 0.80,
                     1, 'critical', 'rules-v1', 'model-v1', CURRENT_TIMESTAMP),
                    ('overall_risk', 'danger', 1, 0.60, 0.40, 0.60,
                     0, NULL, 'rules-v1', 'model-v1', CURRENT_TIMESTAMP),
                    ('auth_strength', 'warning', 0, 0.40, 0.20, 0.30,
                     0, NULL, 'rules-v2', 'model-v1', CURRENT_TIMESTAMP)
                """
            )
        )

    local_app = FastAPI()
    local_app.include_router(router, prefix="/api")

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/scoring/calibration")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["axes"]) == 2, payload
        assert sum(axis["rows"] for axis in payload["axes"]) == 3, payload

        overall = next(
            axis for axis in payload["axes"] if axis["axis_name"] == "overall_risk"
        )
        assert overall["escalated_count"] == 1, payload

        filtered = client.get(
            "/api/scoring/calibration", params={"axis": "overall_risk"}
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert len(filtered_payload["axes"]) == 1, filtered_payload
        assert filtered_payload["axes"][0]["axis_name"] == "overall_risk"
        assert filtered_payload["axes"][0]["rows"] == 2

        invalid = client.get(
            "/api/scoring/calibration", params={"axis": "not_an_axis"}
        )
        assert invalid.status_code == 422, invalid.text

    print("PASS")


if __name__ == "__main__":
    _run_self_test()