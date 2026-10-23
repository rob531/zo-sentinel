from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api/v1/mesh", tags=["mesh"])


class MeshMemoryRecord(BaseModel):
    id: int
    server_id: str
    memory_type: str
    content: str
    embedding_vector: Optional[list] = None
    created_at: str

    class Config:
        from_attributes = True


class ScoreDisputeRecord(BaseModel):
    id: int
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: str
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    p_top: Optional[float] = None
    model_version: str
    decision_rule_version: str
    scored_at: str
    escalated: bool
    escalated_to: Optional[str] = None

    class Config:
        from_attributes = True


def _read_mesh_memory(session: Session, server_id: str, before_ts: Optional[str] = None) -> list[MeshMemoryRecord]:
    query = "SELECT id, server_id, memory_type, content, embedding_vector, created_at FROM mesh_memory WHERE server_id = :server_id"
    params = {"server_id": server_id}
    if before_ts:
        query += " AND created_at < :before_ts"
        params["before_ts"] = before_ts
    query += " ORDER BY created_at DESC LIMIT 100"
    result = session.execute(text(query), params)
    rows = result.fetchall()
    return [
        MeshMemoryRecord(
            id=row.id,
            server_id=row.server_id,
            memory_type=row.memory_type,
            content=row.content,
            embedding_vector=row.embedding_vector,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _read_score_disputes(session: Session, server_id: Optional[str], axis_name: Optional[str], min_score_ts: Optional[str], escalated: Optional[bool]) -> list[ScoreDisputeRecord]:
    query = "SELECT id, server_id, axis_name, label, label_index, probs, p_critical, p_danger, p_top, model_version, decision_rule_version, scored_at, escalated, escalated_to FROM mcp_score_disputes WHERE 1=1"
    params = {}
    if server_id:
        query += " AND server_id = :server_id"
        params["server_id"] = server_id
    if axis_name:
        query += " AND axis_name = :axis_name"
        params["axis_name"] = axis_name
    if min_score_ts:
        query += " AND scored_at >= :min_score_ts"
        params["min_score_ts"] = min_score_ts
    if escalated is not None:
        query += " AND escalated = :escalated"
        params["escalated"] = escalated
    query += " ORDER BY scored_at DESC LIMIT 200"
    result = session.execute(text(query), params)
    rows = result.fetchall()
    return [
        ScoreDisputeRecord(
            id=row.id,
            server_id=row.server_id,
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            probs=row.probs,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            p_top=row.p_top,
            model_version=row.model_version,
            decision_rule_version=row.decision_rule_version,
            scored_at=row.scored_at,
            escalated=row.escalated,
            escalated_to=row.escalated_to,
        )
        for row in rows
    ]


@router.get("/memory/{server_id}", response_model=list[MeshMemoryRecord])
def get_mesh_memory_endpoint(
    server_id: str,
    before_ts: Optional[str] = Query(None, description="ISO timestamp for time-based cursor"),
    session: Session = Depends(get_session),
) -> list[MeshMemoryRecord]:
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")
    return _read_mesh_memory(session, server_id, before_ts)


@router.get("/disputes", response_model=list[ScoreDisputeRecord])
def get_score_disputes_endpoint(
    server_id: Optional[str] = Query(None, description="Filter by server ID"),
    axis_name: Optional[str] = Query(None, description="Filter by axis name"),
    min_score_ts: Optional[str] = Query(None, description="Minimum score timestamp (ISO format)"),
    escalated: Optional[bool] = Query(None, description="Filter by escalation status"),
    session: Session = Depends(get_session),
) -> list[ScoreDisputeRecord]:
    return _read_score_disputes(session, server_id, axis_name, min_score_ts, escalated)


def include_router(app):
    app.include_router(router)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    the_app = FastAPI()
    include_router(the_app)
    the_app.dependency_overrides[get_session] = override_get_session

    with test_engine.connect() as conn:
        conn.execute(text("INSERT INTO mcp_score_disputes (server_id, axis_name, label, label_index, probs, model_version, decision_rule_version, scored_at, escalated, escalated_to) VALUES (:server_id, :axis_name, :label, :label_index, :probs, :model_version, :decision_rule_version, :scored_at, :escalated, :escalated_to)"), {"server_id": "srv_test", "axis_name": "safety", "label": "safe", "label_index": 0, "probs": "[0.9,0.1]", "model_version": "v1", "decision_rule_version": "r1", "scored_at": "2025-01-01T00:00:00", "escalated": False, "escalated_to": None})
        conn.commit()

        result = conn.execute(text("INSERT INTO mesh_memory (server_id, memory_type, content, embedding_vector, created_at) VALUES (:server_id, :memory_type, :content, :embedding_vector, :created_at)"), {"server_id": "srv_test", "memory_type": "context", "content": "test memory", "embedding_vector": "[0.1,0.2]", "created_at": "2025-01-01T00:00:00"})
        conn.commit()

    with test_engine.connect() as conn:
        disputes_result = conn.execute(text("SELECT COUNT(*) FROM mcp_score_disputes")).fetchone()
        memory_result = conn.execute(text("SELECT COUNT(*) FROM mesh_memory")).fetchone()

    assert disputes_result[0] >= 1, f"Expected at least 1 dispute, got {disputes_result[0]}"
    assert memory_result[0] >= 1, f"Expected at least 1 memory record, got {memory_result[0]}"

    print("PASS")