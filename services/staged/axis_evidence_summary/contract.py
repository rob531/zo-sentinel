from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_evidence_summary"])


class AxisEvidenceResponse(BaseModel):
    server_id: str
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    probs: list[float]
    scored_at: datetime


@router.get("/axis/evidence", response_model=list[AxisEvidenceResponse])
def get_axis_evidence(
    server_id: str = Query(..., description="Server ID to filter by"),
    axis_name: str = Query(..., description="Axis name to filter by"),
    session: Any = Depends(get_session),
) -> list[dict[str, Any]]:
    query = text("""
        SELECT
            server_id,
            axis_name,
            label,
            p_top,
            p_critical,
            p_danger,
            escalated,
            probs,
            scored_at
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
          AND axis_name = :axis_name
        ORDER BY scored_at DESC
    """)
    result = session.execute(query, {"server_id": server_id, "axis_name": axis_name})
    rows = result.fetchall()
    
    evidence_list = []
    for row in rows:
        probs_val = row.probs
        if isinstance(probs_val, str):
            probs_val = json.loads(probs_val)
        
        evidence_list.append({
            "server_id": row.server_id,
            "axis_name": row.axis_name,
            "label": row.label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "escalated": row.escalated,
            "probs": probs_val,
            "scored_at": row.scored_at,
        })
    
    return evidence_list


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    
    app = FastAPI()
    app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT NOT NULL,
                label_index INTEGER NOT NULL,
                p_top REAL NOT NULL,
                p_critical REAL NOT NULL,
                p_danger REAL NOT NULL,
                probs TEXT NOT NULL,
                escalated BOOLEAN NOT NULL,
                escalated_to TEXT,
                scored_at TIMESTAMP NOT NULL,
                model_version TEXT NOT NULL,
                adapter_sha256 TEXT NOT NULL,
                decision_rule_version TEXT NOT NULL
            )
        """))
        
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores 
            (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, escalated, escalated_to, scored_at, model_version, adapter_sha256, decision_rule_version)
            VALUES 
            (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :probs, :escalated, :escalated_to, :scored_at, :model_version, :adapter_sha256, :decision_rule_version)
        """), {
            "server_id": "srv_test_001",
            "axis_name": "capability_depth",
            "label": "high_capability",
            "label_index": 2,
            "p_top": 0.75,
            "p_critical": 0.10,
            "p_danger": 0.05,
            "probs": json.dumps([0.10, 0.15, 0.75]),
            "escalated": False,
            "escalated_to": None,
            "scored_at": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            "model_version": "v1.0",
            "adapter_sha256": "abc123",
            "decision_rule_version": "v1"
        })
        
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores 
            (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, escalated, escalated_to, scored_at, model_version, adapter_sha256, decision_rule_version)
            VALUES 
            (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :probs, :escalated, :escalated_to, :scored_at, :model_version, :adapter_sha256, :decision_rule_version)
        """), {
            "server_id": "srv_test_001",
            "axis_name": "capability_depth",
            "label": "medium_capability",
            "label_index": 1,
            "p_top": 0.45,
            "p_critical": 0.20,
            "p_danger": 0.15,
            "probs": json.dumps([0.20, 0.45, 0.35]),
            "escalated": False,
            "escalated_to": None,
            "scored_at": datetime(2024, 1, 15, 11, 30, 0, tzinfo=timezone.utc),
            "model_version": "v1.0",
            "adapter_sha256": "abc123",
            "decision_rule_version": "v1"
        })
    
    def override_get_session():
        with engine.connect() as conn:
            yield conn
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/api/axis/evidence?server_id=srv_test_001&axis_name=capability_depth")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert isinstance(data, list), f"Expected list, got {type(data)}"
    assert len(data) == 2, f"Expected 2 records, got {len(data)}"
    assert "p_top" in data[0], "Missing p_top field"
    assert "probs" in data[0], "Missing probs field"
    assert isinstance(data[0]["probs"], list), f"Expected probs to be list, got {type(data[0]['probs'])}"
    
    print("PASS")