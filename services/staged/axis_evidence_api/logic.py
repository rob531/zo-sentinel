"""Axis Evidence API Logic Module"""

from typing import Optional, Any
from datetime import datetime
from decimal import Decimal
import json

from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisEvidenceResponse(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    probs: list[float]
    escalated: Optional[bool]
    escalated_to: Optional[str]
    decision_rule_version: Optional[str]
    model_version: Optional[str]
    scored_at: datetime

    class Config:
        from_attributes = True


def get_axis_evidence(
    server_id: str,
    axis_name: str,
    scored_after: Optional[datetime] = None
) -> list[AxisEvidenceResponse]:
    """
    Fetch axis evidence records for a given server_id and axis_name.
    Optionally filter by scored_after timestamp.
    """
    results = []
    
    def _fetch(session: Session) -> list[dict]:
        query = text("""
            SELECT 
                server_id,
                axis_name,
                label,
                label_index,
                p_top,
                p_critical,
                p_danger,
                probs,
                escalated,
                escalated_to,
                decision_rule_version,
                model_version,
                scored_at
            FROM McpLlmAxisScore
            WHERE server_id = :server_id
              AND axis_name = :axis_name
        """)
        params = {"server_id": server_id, "axis_name": axis_name}
        
        if scored_after:
            query = text("""
                SELECT 
                    server_id,
                    axis_name,
                    label,
                    label_index,
                    p_top,
                    p_critical,
                    p_danger,
                    probs,
                    escalated,
                    escalated_to,
                    decision_rule_version,
                    model_version,
                    scored_at
                FROM McpLlmAxisScore
                WHERE server_id = :server_id
                  AND axis_name = :axis_name
                  AND scored_at > :scored_after
            """)
            params["scored_after"] = scored_after
        
        result = session.execute(query, params)
        return [dict(row._mapping) for row in result.fetchall()]
    
    with get_session() as session:
        rows = _fetch(session)
    
    for row in rows:
        probs_val = row.get("probs")
        if isinstance(probs_val, str):
            probs_val = json.loads(probs_val)
        elif isinstance(probs_val, (list, tuple)):
            probs_val = list(probs_val)
        else:
            probs_val = []
        
        p_top = row.get("p_top")
        if isinstance(p_top, Decimal):
            p_top = float(p_top)
        
        p_critical = row.get("p_critical")
        if isinstance(p_critical, Decimal):
            p_critical = float(p_critical)
        
        p_danger = row.get("p_danger")
        if isinstance(p_danger, Decimal):
            p_danger = float(p_danger)
        
        results.append(AxisEvidenceResponse(
            server_id=row["server_id"],
            axis_name=row["axis_name"],
            label=row["label"],
            label_index=row["label_index"],
            p_top=p_top,
            p_critical=p_critical,
            p_danger=p_danger,
            probs=probs_val,
            escalated=row.get("escalated"),
            escalated_to=row.get("escalated_to"),
            decision_rule_version=row.get("decision_rule_version"),
            model_version=row.get("model_version"),
            scored_at=row["scored_at"]
        ))
    
    return results


def _row_to_response(row: dict) -> AxisEvidenceResponse:
    """Convert a database row dict to AxisEvidenceResponse."""
    probs_val = row.get("probs")
    if isinstance(probs_val, str):
        probs_val = json.loads(probs_val)
    elif isinstance(probs_val, (list, tuple)):
        probs_val = list(probs_val)
    else:
        probs_val = []
    
    def to_float(v):
        if isinstance(v, Decimal):
            return float(v)
        return v
    
    return AxisEvidenceResponse(
        server_id=row["server_id"],
        axis_name=row["axis_name"],
        label=row["label"],
        label_index=row["label_index"],
        p_top=to_float(row.get("p_top")),
        p_critical=to_float(row.get("p_critical")),
        p_danger=to_float(row.get("p_danger")),
        probs=probs_val,
        escalated=row.get("escalated"),
        escalated_to=row.get("escalated_to"),
        decision_rule_version=row.get("decision_rule_version"),
        model_version=row.get("model_version"),
        scored_at=row["scored_at"]
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    create_query = text("""
        CREATE TABLE IF NOT EXISTS McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id VARCHAR(255) NOT NULL,
            axis_name VARCHAR(255) NOT NULL,
            label VARCHAR(255) NOT NULL,
            label_index INTEGER NOT NULL,
            p_top FLOAT,
            p_critical FLOAT,
            p_danger FLOAT,
            probs TEXT,
            escalated BOOLEAN,
            escalated_to VARCHAR(255),
            decision_rule_version VARCHAR(255),
            model_version VARCHAR(255),
            scored_at TIMESTAMP NOT NULL,
            adapter_sha256 VARCHAR(255)
        )
    """)
    
    with engine.connect() as conn:
        conn.execute(create_query)
        conn.commit()
    
    seed_data = [
        {
            "server_id": "test1",
            "axis_name": "overall_risk",
            "label": "high",
            "label_index": 2,
            "p_top": 0.15,
            "p_critical": 0.45,
            "p_danger": 0.75,
            "probs": json.dumps([0.05, 0.15, 0.45, 0.35]),
            "escalated": True,
            "escalated_to": "security_team",
            "decision_rule_version": "v1.0",
            "model_version": "gpt-4",
            "scored_at": "2024-01-15T10:30:00",
            "adapter_sha256": "abc123"
        },
        {
            "server_id": "test1",
            "axis_name": "reliability",
            "label": "medium",
            "label_index": 1,
            "p_top": 0.30,
            "p_critical": 0.50,
            "p_danger": 0.20,
            "probs": json.dumps([0.10, 0.30, 0.50, 0.10]),
            "escalated": False,
            "escalated_to": None,
            "decision_rule_version": "v1.0",
            "model_version": "gpt-4",
            "scored_at": "2024-01-15T10:30:00",
            "adapter_sha256": "abc123"
        }
    ]
    
    insert_query = text("""
        INSERT INTO McpLlmAxisScore 
        (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, escalated, escalated_to, decision_rule_version, model_version, scored_at, adapter_sha256)
        VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :probs, :escalated, :escalated_to, :decision_rule_version, :model_version, :scored_at, :adapter_sha256)
    """)
    
    with engine.connect() as conn:
        for row in seed_data:
            conn.execute(insert_query, row)
        conn.commit()
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    
    @app.get("/api/servers/{server_id}/axes/{axis_name}/evidence")
    def fetch_axis_evidence(
        server_id: str,
        axis_name: str,
        scored_after: Optional[str] = None
    ):
        scored_after_dt = None
        if scored_after:
            scored_after_dt = datetime.fromisoformat(scored_after)
        
        db_session = next(override_get_session())
        try:
            query = text("""
                SELECT 
                    server_id,
                    axis_name,
                    label,
                    label_index,
                    p_top,
                    p_critical,
                    p_danger,
                    probs,
                    escalated,
                    escalated_to,
                    decision_rule_version,
                    model_version,
                    scored_at
                FROM McpLlmAxisScore
                WHERE server_id = :server_id
                  AND axis_name = :axis_name
            """)
            params = {"server_id": server_id, "axis_name": axis_name}
            
            if scored_after_dt:
                query = text("""
                    SELECT 
                        server_id,
                        axis_name,
                        label,
                        label_index,
                        p_top,
                        p_critical,
                        p_danger,
                        probs,
                        escalated,
                        escalated_to,
                        decision_rule_version,
                        model_version,
                        scored_at
                    FROM McpLlmAxisScore
                    WHERE server_id = :server_id
                      AND axis_name = :axis_name
                      AND scored_at > :scored_after
                """)
                params["scored_after"] = scored_after_dt.isoformat()
            
            result = db_session.execute(query, params)
            rows = [dict(row._mapping) for row in result.fetchall()]
            
            responses = []
            for row in rows:
                probs_val = row.get("probs")
                if isinstance(probs_val, str):
                    probs_val = json.loads(probs_val)
                elif isinstance(probs_val, (list, tuple)):
                    probs_val = list(probs_val)
                else:
                    probs_val = []
                
                def to_float(v):
                    if isinstance(v, Decimal):
                        return float(v)
                    return v
                
                responses.append(AxisEvidenceResponse(
                    server_id=row["server_id"],
                    axis_name=row["axis_name"],
                    label=row["label"],
                    label_index=row["label_index"],
                    p_top=to_float(row.get("p_top")),
                    p_critical=to_float(row.get("p_critical")),
                    p_danger=to_float(row.get("p_danger")),
                    probs=probs_val,
                    escalated=row.get("escalated"),
                    escalated_to=row.get("escalated_to"),
                    decision_rule_version=row.get("decision_rule_version"),
                    model_version=row.get("model_version"),
                    scored_at=row["scored_at"]
                ))
            
            return responses
        finally:
            db_session.close()
    
    client = TestClient(app)
    
    response = client.get("/api/servers/test1/axes/overall_risk/evidence")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert isinstance(data, list), "Expected list response"
    assert len(data) > 0, "Expected at least one record"
    
    record = data[0]
    assert "p_top" in record, "p_top field must be present"
    assert record["p_top"] is not None, "p_top must be non-null"
    assert "escalated" in record, "escalated field must be present"
    
    print("PASS")