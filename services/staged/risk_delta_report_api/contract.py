from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk"])

class AxisDelta(BaseModel):
    axis_name: str
    previous_p_top: Optional[float]
    current_p_top: Optional[float]
    delta: float
    direction: str

class RiskDeltaResponse(BaseModel):
    server_id: str
    axes: list[AxisDelta]
    overall_delta: float
    scored_at_current: datetime
    scored_at_previous: datetime

@router.get("/risk/delta", response_model=RiskDeltaResponse)
def get_risk_delta(
    server_id: str,
    session: Session = Depends(get_session)
) -> RiskDeltaResponse:
    result = session.execute(
        text("""
            SELECT DISTINCT scored_at
            FROM McpLlmAxisScore
            WHERE server_id = :server_id
            ORDER BY scored_at DESC
            LIMIT 2
        """),
        {"server_id": server_id}
    ).fetchall()

    if len(result) < 2:
        raise HTTPException(
            status_code=404,
            detail=f"Not enough scoring history for server {server_id}"
        )

    scored_at_current = result[0][0]
    scored_at_previous = result[1][0]

    scores_result = session.execute(
        text("""
            SELECT axis_name, scored_at, p_top
            FROM McpLlmAxisScore
            WHERE server_id = :server_id
              AND scored_at IN (:scored_at_current, :scored_at_previous)
            ORDER BY axis_name, scored_at DESC
        """),
        {"server_id": server_id, "scored_at_current": scored_at_current, "scored_at_previous": scored_at_previous}
    ).fetchall()

    axis_data = {}
    for row in scores_result:
        axis_name, scored_at, p_top = row
        if axis_name not in axis_data:
            axis_data[axis_name] = {"current": None, "previous": None}
        if scored_at == scored_at_current:
            axis_data[axis_name]["current"] = p_top
        else:
            axis_data[axis_name]["previous"] = p_top

    axes = []
    for axis_name, data in axis_data.items():
        current_p_top = data["current"]
        previous_p_top = data["previous"]
        delta = (current_p_top or 0) - (previous_p_top or 0)
        direction = "increase" if delta > 0 else "decrease" if delta < 0 else "no_change"
        axes.append(AxisDelta(
            axis_name=axis_name,
            previous_p_top=previous_p_top,
            current_p_top=current_p_top,
            delta=delta,
            direction=direction
        ))

    overall_delta = sum(ax.delta for ax in axes)

    return RiskDeltaResponse(
        server_id=server_id,
        axes=axes,
        overall_delta=overall_delta,
        scored_at_current=scored_at_current,
        scored_at_previous=scored_at_previous
    )


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY,
                adapter_sha256 TEXT,
                axis_name TEXT NOT NULL,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                scored_at TIMESTAMP NOT NULL,
                server_id TEXT NOT NULL
            )
        """))

    session = SessionLocal()
    first_timestamp = datetime(2025, 6, 1, 12, 0, 0)
    second_timestamp = datetime(2025, 7, 1, 12, 0, 0)

    srv1_records = [
        (first_timestamp, 'bias', 0.5, 'medium', 1, '[0.3,0.5,0.2]', 0.2, 0.3, 'v1', 'rule_v1', 'sha1', False, None),
        (first_timestamp, 'injection', 0.3, 'low', 0, '[0.6,0.3,0.1]', 0.1, 0.2, 'v1', 'rule_v1', 'sha2', False, None),
        (first_timestamp, 'safety', 0.2, 'low', 2, '[0.7,0.2,0.1]', 0.05, 0.15, 'v1', 'rule_v1', 'sha3', False, None),
        (second_timestamp, 'bias', 0.4, 'medium', 1, '[0.4,0.4,0.2]', 0.2, 0.3, 'v2', 'rule_v2', 'sha1b', False, None),
        (second_timestamp, 'injection', 0.5, 'medium', 1, '[0.2,0.5,0.3]', 0.3, 0.4, 'v2', 'rule_v2', 'sha2b', False, None),
        (second_timestamp, 'safety', 0.1, 'low', 2, '[0.8,0.1,0.1]', 0.02, 0.1, 'v2', 'rule_v2', 'sha3b', False, None),
    ]

    srv2_records = [
        (first_timestamp, 'bias', 0.6, 'high', 2, '[0.2,0.4,0.4]', 0.4, 0.5, 'v1', 'rule_v1', 'sha4', False, None),
        (first_timestamp, 'injection', 0.5, 'medium', 1, '[0.3,0.4,0.3]', 0.3, 0.4, 'v1', 'rule_v1', 'sha5', False, None),
        (first_timestamp, 'safety', 0.3, 'low', 1, '[0.5,0.3,0.2]', 0.15, 0.25, 'v1', 'rule_v1', 'sha6', False, None),
        (second_timestamp, 'bias', 0.3, 'low', 0, '[0.6,0.3,0.1]', 0.15, 0.2, 'v2', 'rule_v2', 'sha4b', False, None),
        (second_timestamp, 'injection', 0.4, 'low', 0, '[0.4,0.4,0.2]', 0.2, 0.3, 'v2', 'rule_v2', 'sha5b', False, None),
        (second_timestamp, 'safety', 0.25, 'low', 1, '[0.55,0.3,0.15]', 0.1, 0.2, 'v2', 'rule_v2', 'sha6b', False, None),
    ]

    for server_id, records in [('srv_001', srv1_records), ('srv_002', srv2_records)]:
        for ts, axis, p_top, label, idx, probs, p_crit, p_dang, mv, drv, ash, esc, esc_to in records:
            session.execute(text("""
                INSERT INTO McpLlmAxisScore 
                (server_id, scored_at, axis_name, p_top, label, label_index, probs, p_critical, p_danger, model_version, decision_rule_version, adapter_sha256, escalated, escalated_to)
                VALUES (:server_id, :scored_at, :axis_name, :p_top, :label, :label_index, :probs, :p_critical, :p_danger, :model_version, :decision_rule_version, :adapter_sha256, :escalated, :escalated_to)
            """), {
                'server_id': server_id, 'scored_at': ts, 'axis_name': axis, 'p_top': p_top,
                'label': label, 'label_index': idx, 'probs': probs, 'p_critical': p_crit,
                'p_danger': p_dang, 'model_version': mv, 'decision_rule_version': drv,
                'adapter_sha256': ash, 'escalated': esc, 'escalated_to': esc_to
            })
    session.commit()
    session.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/risk/delta", params={"server_id": "srv_001"})
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "srv_001"
    non_zero_deltas = [ax for ax in data["axes"] if ax["delta"] != 0]
    assert len(non_zero_deltas) > 0, f"Expected at least one non-zero delta, got: {data['axes']}"

    print("PASS")