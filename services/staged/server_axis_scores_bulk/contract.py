from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class AxisScore(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: float
    p_critical: float
    p_danger: float
    probs: Optional[List[float]] = None
    escalated: bool = False
    scored_at: Optional[datetime] = None


class ServerAxisScores(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    axes: List[AxisScore]


class ServerAxisScoresResponse(BaseModel):
    servers: List[ServerAxisScores]


@router.get("/api/servers/axis-scores", response_model=ServerAxisScoresResponse)
def get_axis_scores(
    server_ids: str = Query(..., description="Comma-separated server IDs"),
    session: Session = Depends(get_session),
):
    id_list = [s.strip() for s in server_ids.split(",") if s.strip()]
    
    servers_data = {}
    
    for sid in id_list:
        registry = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == sid
        ).first()
        
        if registry:
            servers_data[sid] = {
                "server_id": sid,
                "server_name": registry.name,
                "axes": []
            }
    
    if servers_data:
        axis_scores = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id.in_(list(servers_data.keys()))
        ).all()
        
        for score in axis_scores:
            sid = score.server_id
            if sid in servers_data:
                probs = None
                if score.probs:
                    try:
                        probs = json.loads(score.probs) if isinstance(score.probs, str) else score.probs
                    except (json.JSONDecodeError, TypeError):
                        probs = None
                
                axis = AxisScore(
                    axis_name=score.axis_name,
                    label=score.label,
                    p_top=score.p_top,
                    p_critical=score.p_critical,
                    p_danger=score.p_danger,
                    probs=probs,
                    escalated=score.escalated,
                    scored_at=score.scored_at,
                )
                servers_data[sid]["axes"].append(axis)
    
    return ServerAxisScoresResponse(
        servers=list(servers_data.values())
    )


if __name__ == "__main__":
    import sys
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    db = TestingSessionLocal()
    
    server_data = [
        {"server_id": "server_1", "name": "Test Server 1", "url": "http://localhost:9001"},
        {"server_id": "server_2", "name": "Test Server 2", "url": "http://localhost:9002"},
    ]
    
    for sd in server_data:
        server = McpServerRegistry(
            server_id=sd["server_id"],
            name=sd["name"],
            url=sd["url"],
            registry_source="test",
            description=f"Test description for {sd['server_id']}",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            last_scanned=datetime.now(timezone.utc),
            last_assessed=datetime.now(timezone.utc),
            confidence=0.95,
            trust_score=88.5,
            risk_tier="low",
            scan_count=15,
            verdict="operational",
            verdict_reasoning="Test server operational",
            meta="{}",
        )
        db.add(server)
    
    axis_names = [
        "accuracy", "reliability", "latency", "cost", "scalability", "security", "maintainability"
    ]
    
    for sd in server_data:
        for idx, axis_name in enumerate(axis_names):
            axis_score = McpLlmAxisScore(
                id=str(uuid.uuid4()),
                server_id=sd["server_id"],
                adapter_sha256="abcd1234efgh5678ijkl9012mnop3456",
                axis_name=axis_name,
                label=f"Label_{idx}",
                label_index=idx,
                model_version="v1.0.0",
                decision_rule_version="dr_v1",
                p_top=0.85 + (idx * 0.02),
                p_critical=0.08 - (idx * 0.008),
                p_danger=0.05 - (idx * 0.004),
                probs=json.dumps([0.85, 0.10, 0.03, 0.02]),
                escalated=False,
                escalated_to=None,
                scored_at=datetime.now(timezone.utc),
            )
            db.add(axis_score)
    
    db.commit()
    db.close()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response = client.get("/api/servers/axis-scores?server_ids=server_1,server_2")
    
    if response.status_code != 200:
        print(f"FAIL: status {response.status_code}")
        sys.exit(1)
    
    data = response.json()
    
    if "servers" not in data:
        print("FAIL: missing servers")
        sys.exit(1)
    
    servers = data["servers"]
    found_ids = {s["server_id"] for s in servers}
    
    if "server_1" not in found_ids or "server_2" not in found_ids:
        print("FAIL: missing server_ids")
        sys.exit(1)
    
    for server in servers:
        if len(server["axes"]) != 7:
            print(f"FAIL: {server['server_id']} has {len(server['axes'])} axes")
            sys.exit(1)
        
        for axis in server["axes"]:
            if not isinstance(axis.get("p_top"), float):
                print(f"FAIL: p_top not float")
                sys.exit(1)
            if not isinstance(axis.get("p_critical"), float):
                print(f"FAIL: p_critical not float")
                sys.exit(1)
    
    print("PASS")
    sys.exit(0)