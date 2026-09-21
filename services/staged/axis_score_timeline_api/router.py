# services/staged/axis_score_timeline_api/router.py
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Dict
from datetime import datetime, timedelta
from collections import defaultdict

from app.db import get_session
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["axis_score_timeline"])


class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float


class AxisDataPoint(BaseModel):
    scored_at: datetime
    axes: Dict[str, AxisScore]


class AxisTimelineResponse(BaseModel):
    server_id: str
    days: int
    series: List[AxisDataPoint]


@router.get("/axis/timeline", response_model=AxisTimelineResponse)
def get_axis_timeline(
    server_id: str = Query(...),
    days: int = Query(7),
    session: Session = Depends(get_session)
) -> AxisTimelineResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    sql = text("""
        SELECT scored_at, axis_name, label, p_top, p_critical, p_danger
        FROM McpLlmAxisScore
        WHERE server_id = :server_id
          AND scored_at >= :cutoff
        ORDER BY scored_at ASC, axis_name ASC
    """)
    
    rows = session.execute(sql, {"server_id": server_id, "cutoff": cutoff}).fetchall()
    
    points: Dict[datetime, Dict[str, AxisScore]] = defaultdict(dict)
    for row in rows:
        points[row.scored_at][row.axis_name] = AxisScore(
            label=row.label,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger
        )
    
    series = [
        AxisDataPoint(scored_at=ts, axes=axes)
        for ts, axes in sorted(points.items())
    ]
    
    return AxisTimelineResponse(server_id=server_id, days=days, series=series)


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    create_sql = text("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY,
            adapter_sha256 TEXT,
            axis_name TEXT NOT NULL,
            decision_rule_version TEXT,
            escalated INTEGER,
            escalated_to TEXT,
            label TEXT NOT NULL,
            label_index INTEGER,
            model_version TEXT,
            p_critical REAL,
            p_danger REAL,
            p_top REAL,
            probs TEXT,
            scored_at TIMESTAMP NOT NULL,
            server_id TEXT NOT NULL
        )
    """)
    with test_engine.connect() as conn:
        conn.execute(create_sql)
        conn.commit()
    
    now = datetime.utcnow()
    
    seeds = [
        # server_a
        (now, "server_a", "tier", "tier_1", 0.85, 0.10, 0.05),
        (now, "server_a", "velocity", "velocity_low", 0.90, 0.08, 0.02),
        (now - timedelta(hours=4), "server_a", "tier", "tier_2", 0.60, 0.25, 0.15),
        (now - timedelta(hours=4), "server_a", "velocity", "velocity_med", 0.55, 0.30, 0.15),
        (now - timedelta(hours=8), "server_a", "tier", "tier_3", 0.30, 0.40, 0.30),
        (now - timedelta(hours=8), "server_a", "velocity", "velocity_high", 0.25, 0.35, 0.40),
        # server_b
        (now, "server_b", "tier", "tier_1", 0.70, 0.20, 0.10),
        (now, "server_b", "velocity", "velocity_low", 0.75, 0.15, 0.10),
        (now - timedelta(hours=6), "server_b", "tier", "tier_2", 0.50, 0.30, 0.20),
        (now - timedelta(hours=6), "server_b", "velocity", "velocity_med", 0.45, 0.35, 0.20),
        (now - timedelta(hours=12), "server_b", "tier", "tier_3", 0.20, 0.35, 0.45),
        (now - timedelta(hours=12), "server_b", "velocity", "velocity_high", 0.15, 0.30, 0.55),
    ]
    
    insert_sql = text("""
        INSERT INTO McpLlmAxisScore 
        (scored_at, server_id, axis_name, label, p_top, p_critical, p_danger)
        VALUES (:scored_at, :server_id, :axis_name, :label, :p_top, :p_critical, :p_danger)
    """)
    
    with test_engine.connect() as conn:
        for s in seeds:
            conn.execute(insert_sql, {
                "scored_at": s[0],
                "server_id": s[1],
                "axis_name": s[2],
                "label": s[3],
                "p_top": s[4],
                "p_critical": s[5],
                "p_danger": s[6]
            })
        conn.commit()
    
    TestingSessionLocal = sessionmaker(bind=test_engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    
    response = client.get("/api/axis/timeline?server_id=server_a&days=1")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert len(data["series"]) >= 3, f"Expected series length >= 3, got {len(data['series'])}"
    
    found_known_p_top = False
    for point in data["series"]:
        for axis_name, axis_data in point["axes"].items():
            if axis_data["p_top"] == 0.85:
                found_known_p_top = True
                break
    
    assert found_known_p_top, "Expected known p_top value 0.85 not found"
    
    print("PASS")