from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_risk_tier_summary"])


class AxisScoreDetail(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float


class ServerRiskTierSummaryResponse(BaseModel):
    server_id: str
    overall_risk: float
    risk_tier: str
    axis_scores: dict[str, AxisScoreDetail]


@router.get("/server/{server_id}/risk_tier_summary", response_model=ServerRiskTierSummaryResponse)
def get_server_risk_tier_summary(
    server_id: str,
    session: Session = Depends(get_session)
) -> dict[str, Any]:
    result = session.execute(
        text("SELECT name FROM McpServerRegistry WHERE server_id = :server_id"),
        {"server_id": server_id}
    ).fetchone()
    
    scores_result = session.execute(
        text("""
            SELECT axis_name, label, p_top, p_critical, p_danger 
            FROM McpLlmAxisScore 
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    ).fetchall()
    
    axis_scores: dict[str, AxisScoreDetail] = {}
    overall_risk: float = 0.0
    risk_tier: str = "LOW"
    
    for row in scores_result:
        axis_name, label, p_top, p_critical, p_danger = row
        axis_scores[axis_name] = AxisScoreDetail(
            label=label,
            p_top=float(p_top),
            p_critical=float(p_critical),
            p_danger=float(p_danger)
        )
        if axis_name == "overall_risk":
            overall_risk = float(p_top)
            if p_critical > 0.5:
                risk_tier = "CRITICAL"
            elif p_danger > 0.5:
                risk_tier = "DANGER"
            elif p_top > 0.5:
                risk_tier = "HIGH"
    
    if "CRITICAL" in axis_scores and risk_tier != "CRITICAL":
        risk_tier = "CRITICAL"
    
    return ServerRiskTierSummaryResponse(
        server_id=server_id,
        overall_risk=overall_risk,
        risk_tier=risk_tier,
        axis_scores=axis_scores
    )


if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from app.main import app
    
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE McpServerRegistry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            label TEXT NOT NULL,
            p_top REAL NOT NULL,
            p_critical REAL NOT NULL,
            p_danger REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(server_id, axis_name)
        )
    """)
    
    cursor.execute(
        "INSERT INTO McpServerRegistry (server_id, name, status) VALUES (?, ?, ?)",
        ("server-001", "Test Server", "active")
    )
    
    cursor.execute(
        "INSERT INTO McpLlmAxisScore (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES (?, ?, ?, ?, ?, ?)",
        ("server-001", "overall_risk", "Overall Risk", 0.3, 0.7, 0.0)
    )
    cursor.execute(
        "INSERT INTO McpLlmAxisScore (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES (?, ?, ?, ?, ?, ?)",
        ("server-001", "data_exposure", "Data Exposure", 0.2, 0.5, 0.3)
    )
    cursor.execute(
        "INSERT INTO McpLlmAxisScore (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES (?, ?, ?, ?, ?, ?)",
        ("server-001", "availability", "Availability", 0.6, 0.3, 0.1)
    )
    conn.commit()
    
    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False}
    )
    
    for table_name in ["McpServerRegistry", "McpLlmAxisScore"]:
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        
        for row in rows:
            placeholders = ", ".join(["?" for _ in columns])
            cursor.execute(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})", row)
        conn.execute(text(f"DELETE FROM {table_name}"))
        for row in rows:
            placeholders = ", ".join([f":{col}" for col in columns])
            conn.execute(
                text(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"),
                dict(zip(columns, row))
            )
        conn.commit()
    
    conn.close()
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response = client.get("/api/server/server-001/risk_tier_summary")
    
    data = response.json()
    
    assert data["server_id"] == "server-001"
    assert data["overall_risk"] == 0.3
    assert data["risk_tier"] == "CRITICAL"
    assert "overall_risk" in data["axis_scores"]
    assert "data_exposure" in data["axis_scores"]
    assert "availability" in data["axis_scores"]
    
    app.dependency_overrides.clear()
    
    print("PASS")