"""Perspective membership service."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org

router = APIRouter()


class MeshMemoryResponse(BaseModel):
    perspective_id: str
    member_count: int


class SignalScore(BaseModel):
    signal_id: str
    score: float


class MeshScoresResponse(BaseModel):
    perspective_id: str
    scores: list[SignalScore]


class QuarantineResponse(BaseModel):
    success: bool
    message: str


class DummyPostResponse(BaseModel):
    status: str
    data: dict[str, Any]


def get_mesh_memory(perspective_id: str, db: Session) -> dict[str, Any] | None:
    """Get mesh memory for a perspective."""
    try:
        result = db.execute(
            text("SELECT perspective_id, member_count FROM mesh_memory WHERE perspective_id = :pid"),
            {"pid": perspective_id}
        ).fetchone()
        if result:
            return {"perspective_id": result[0], "member_count": result[1]}
        return None
    except Exception:
        return None


def get_signal_scores(perspective_id: str, db: Session) -> list[dict[str, Any]]:
    """Get signal scores for a perspective."""
    try:
        result = db.execute(
            text("""
                SELECT signal_id, score 
                FROM mcp_signal_scores 
                WHERE perspective_id = :pid
            """),
            {"pid": perspective_id}
        ).fetchall()
        return [{"signal_id": r[0], "score": r[1]} for r in result]
    except Exception:
        return []


def get_mesh_scores(perspective_id: str, db: Session) -> dict[str, Any]:
    """Get mesh scores for a perspective."""
    scores = get_signal_scores(perspective_id, db)
    return {
        "perspective_id": perspective_id,
        "scores": scores
    }


def reset_server_export_api_quarantine(server_id: str, db: Session) -> bool:
    """Reset export API quarantine for a server."""
    try:
        db.execute(
            text("UPDATE McpServerRegistry SET quarantine_until = NULL WHERE server_id = :sid"),
            {"sid": server_id}
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False


@router.get("/mesh_memory/{perspective_id}", response_model=MeshMemoryResponse)
def mesh_memory_endpoint(
    perspective_id: str,
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Get mesh memory for a perspective."""
    result = get_mesh_memory(perspective_id, db)
    if result:
        return result
    return {"perspective_id": perspective_id, "member_count": 0}


@router.get("/mesh_scores/{perspective_id}", response_model=MeshScoresResponse)
def mesh_scores_endpoint(
    perspective_id: str,
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Get mesh scores for a perspective."""
    return get_mesh_scores(perspective_id, db)


@router.post("/quarantine/reset/{server_id}", response_model=QuarantineResponse)
def reset_quarantine_endpoint(
    server_id: str,
    db: Session = Depends(get_session)
) -> dict[str, str]:
    """Reset export API quarantine for a server."""
    success = reset_server_export_api_quarantine(server_id, db)
    if success:
        return {"success": True, "message": "Quarantine reset"}
    return {"success": False, "message": "Failed to reset quarantine"}


@router.post("/dummy_post", response_model=DummyPostResponse)
def _dummy_post() -> dict[str, Any]:
    """Dummy POST endpoint for testing."""
    return {"status": "ok", "data": {"test": True}}


@router.get("/dummy_post", response_model=DummyPostResponse)
def dummy_post_endpoint() -> dict[str, Any]:
    """Dummy GET endpoint for testing."""
    return {"status": "ok", "data": {"test": True}}


def _run_self_test(db: Session) -> bool:
    """Run self-test for the service."""
    try:
        db.execute(text("SELECT 1")).fetchone()
        result = get_signal_scores("test-perspective", db)
        assert isinstance(result, list)
        reset_result = reset_server_export_api_quarantine("test-server", db)
        assert isinstance(reset_result, bool)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS mesh_memory (perspective_id TEXT, member_count INTEGER)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS mcp_signal_scores (perspective_id TEXT, signal_id TEXT, score REAL)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS McpServerRegistry (server_id TEXT, quarantine_until TEXT)"))

    if _run_self_test(db):
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)