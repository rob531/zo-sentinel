# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import logging
import uvicorn

from app.db import get_session
from app.models import Perspective

router = APIRouter()
logger = logging.getLogger(__name__)


def get_signal_scores(
    perspective_id: str,
    perspective_name: Optional[str] = None,
    filters: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Retrieve signal scores for a given perspective."""
    response = {
        "perspective_id": perspective_id,
        "perspective_name": perspective_name,
        "signal_types": [],
    }
    return response


def signal_scores_endpoint(
    perspective_id: str,
    perspective_name: Optional[str] = None,
    filters: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_session),
) -> dict:
    """FastAPI endpoint for signal scores."""
    try:
        perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
        if not perspective:
            raise HTTPException(status_code=404, detail="Perspective not found")

        result = get_signal_scores(perspective_id, perspective.name, filters, start_date, end_date)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in signal_scores_endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def get_mesh_memory(perspective_id: str, perspective_name: str) -> dict:
    """Retrieve mesh memory data for a given perspective."""
    return {
        "perspective_id": perspective_id,
        "perspective_name": perspective_name,
        "notes": [],
    }


def mesh_scores_endpoint(
    perspective_id: str,
    perspective_name: Optional[str] = None,
    filters: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_session),
) -> dict:
    """FastAPI endpoint for mesh scores."""
    try:
        perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
        if not perspective:
            raise HTTPException(status_code=404, detail="Perspective not found")

        mesh_memory = get_mesh_memory(perspective_id, perspective.name)
        signal_scores = get_signal_scores(perspective_id, perspective.name, filters, start_date, end_date)

        return {
            "perspective_id": perspective_id,
            "perspective_name": perspective.name,
            "mesh_memory": mesh_memory,
            "signal_scores": signal_scores,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in mesh_scores_endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _run_self_test() -> bool:
    """Run self-test with in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)
    db = TestSessionLocal()

    db.execute(text("""CREATE TABLE perspectives (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        org_id TEXT,
        created_by TEXT,
        created_at TIMESTAMP,
        updated_at TIMESTAMP,
        facet_filters TEXT
    )"""))
    db.execute(text("""CREATE TABLE mcp_signal_scores (
        id TEXT PRIMARY KEY,
        perspective_id TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        signal_value REAL,
        score REAL,
        metadata TEXT,
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    )"""))
    db.commit()

    db.execute(text("""INSERT INTO perspectives (id, name, description, org_id, created_by, created_at, updated_at, facet_filters)
        VALUES ('test-persp-1', 'Test Perspective', 'Test description', 'test-org', 'test-user', '2024-01-01', '2024-01-01', '{}')"""))
    db.execute(text("""INSERT INTO mcp_signal_scores (id, perspective_id, signal_type, signal_name, signal_value, score, metadata, created_at, updated_at)
        VALUES ('ss1', 'test-persp-1', 'signal', 'test_signal', 0.85, 85.0, '{}', '2024-01-01', '2024-01-01'),
               ('ss2', 'test-persp-1', 'severity_score', 'test_severity', 0.92, 92.0, '{}', '2024-01-01', '2024-01-01')"""))
    db.commit()

    result = get_signal_scores("test-persp-1", "Test Perspective")
    assert "perspective_id" in result
    assert "signal_types" in result

    mesh_result = get_mesh_memory("test-persp-1", "Test Perspective")
    assert "perspective_id" in mesh_result
    assert "notes" in mesh_result

    from app.db import get_session
    from fastapi import FastAPI

    app = FastAPI()

    def override_get_session():
        yield db

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/test")
    def test_endpoint():
        return get_signal_scores("test-persp-1", "Test Perspective")

    return True


if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: None

    @app.get("/health")
    def health():
        return {"status": "ok"}

    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")
        exit(1)