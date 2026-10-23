import logging
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute

logger = logging.getLogger(__name__)

router = APIRouter()


class MeshMemoryEndpointResponse(BaseModel):
    data: Any


@router.get("/mesh_memory_endpoint", response_model=MeshMemoryEndpointResponse)
def mesh_memory_endpoint() -> dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory ORDER BY created_at DESC LIMIT 100"},
            timeout=30
        )
        response.raise_for_status()
        return {"data": response.json()}
    except requests.Timeout:
        logger.error("Mesh memory request timed out")
        raise HTTPException(status_code=504, detail="Request timed out")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch mesh memory: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch mesh memory")


@router.get("/signal_scores_endpoint")
def signal_scores_endpoint(session: Session = Depends(get_session)) -> dict:
    try:
        scores = session.query(McpLlmAxisScore).limit(100).all()
        serialized = [
            {
                "id": s.id,
                "score": s.score,
                "axis": s.axis,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            for s in scores
        ]
        return {"data": serialized}
    except Exception as e:
        logger.error(f"Failed to fetch signal scores: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch signal scores")


@router.get("/get_score_disputes_endpoint")
def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> dict:
    try:
        disputes = session.query(McpScoreDispute).limit(100).all()
        serialized = [
            {
                "id": d.id,
                "dispute_type": d.dispute_type,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None
            }
            for d in disputes
        ]
        return {"data": serialized}
    except Exception as e:
        logger.error(f"Failed to fetch score disputes: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch score disputes")


if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"id": "mm1", "data": "Memory 1", "created_at": "2024-01-01T00:00:00"},
        {"id": "mm2", "data": "Memory 2", "created_at": "2024-01-02T00:00:00"}
    ]

    with patch("requests.post", return_value=mock_response):
        app.dependency_overrides[get_session] = override_get_session
        with TestClient(app) as client:
            response = client.get("/mesh_memory_endpoint")
            data = response.json()
            assert "data" in data
            assert isinstance(data["data"], list)
            assert data["data"][0]["id"] == "mm1"
            print("PASS")