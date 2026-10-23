"""Auto-emitted service package."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SignalScoresResponse(BaseModel):
    scores: list[dict[str, Any]]


class MeshMemoryResponse(BaseModel):
    memory: dict[str, Any]


class QuarantineResponse(BaseModel):
    success: bool
    message: str


router = APIRouter()


@router.get("/signal-scores")
def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    query = text("""
        SELECT signal_type, score, confidence, org_id
        FROM mcp_signal_scores
        ORDER BY created_at DESC
        LIMIT 100
    """)
    result = session.execute(query)
    rows = result.fetchall()
    scores = [
        {"signal_type": r[0], "score": r[1], "confidence": r[2], "org_id": r[3]}
        for r in rows
    ]
    return SignalScoresResponse(scores=scores)


@router.get("/mesh-memory")
def mesh_memory_endpoint(
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    query = text("SELECT key, value, updated_at FROM mesh_memory ORDER BY updated_at DESC LIMIT 50")
    result = session.execute(query)
    rows = result.fetchall()
    memory = {r[0]: {"value": r[1], "updated_at": str(r[2])} for r in rows}
    return MeshMemoryResponse(memory=memory)


@router.get("/mesh-scores")
def mesh_scores_endpoint(
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    query = text("""
        SELECT signal_type, score, confidence, org_id
        FROM mcp_signal_scores
        WHERE signal_type LIKE 'mesh%'
        ORDER BY created_at DESC
        LIMIT 100
    """)
    result = session.execute(query)
    rows = result.fetchall()
    scores = [
        {"signal_type": r[0], "score": r[1], "confidence": r[2], "org_id": r[3]}
        for r in rows
    ]
    return SignalScoresResponse(scores=scores)


def get_signal_scores(session: Session) -> list[dict[str, Any]]:
    query = text("SELECT signal_type, score, confidence, org_id FROM mcp_signal_scores")
    result = session.execute(query)
    rows = result.fetchall()
    return [
        {"signal_type": r[0], "score": r[1], "confidence": r[2], "org_id": r[3]}
        for r in rows
    ]


def get_mesh_memory(session: Session) -> dict[str, Any]:
    query = text("SELECT key, value FROM mesh_memory")
    result = session.execute(query)
    rows = result.fetchall()
    return {r[0]: r[1] for r in rows}


@router.post("/reset-quarantine")
def reset_quarantine_endpoint(
    session: Session = Depends(get_session),
) -> QuarantineResponse:
    query = text("DELETE FROM mcp_quarantine WHERE status = 'quarantined'")
    session.execute(query)
    session.commit()
    return QuarantineResponse(success=True, message="Quarantine reset complete")


def _run_self_test(session: Session) -> bool:
    try:
        get_signal_scores(session)
        get_mesh_memory(session)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    session = Session()
    session.execute(text("CREATE TABLE mcp_signal_scores (signal_type TEXT, score REAL, confidence REAL, org_id TEXT, created_at TIMESTAMP)"))
    session.execute(text("CREATE TABLE mesh_memory (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)"))
    session.execute(text("CREATE TABLE mcp_quarantine (id INTEGER PRIMARY KEY, status TEXT)"))
    session.commit()

    from app.db import get_session as original_get_session

    def override_get_session():
        return session

    from app import db as app_db
    app_db.get_session = override_get_session

    if _run_self_test(session):
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)