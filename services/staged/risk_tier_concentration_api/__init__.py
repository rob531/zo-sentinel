from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Optional, Any
import httpx

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)


class SignalScore(BaseModel):
    signal_id: str
    score: float
    category: Optional[str] = None
    tags: Optional[list[str]] = None


class SignalScoresResponse(BaseModel):
    scores: list[SignalScore]
    total: int


class MeshMemoryEntry(BaseModel):
    key: str
    value: Any
    timestamp: Optional[str] = None


class MeshMemoryResponse(BaseModel):
    entries: list[MeshMemoryEntry]
    count: int


class LlmAxisScore(BaseModel):
    axis: str
    score: float
    metadata: Optional[dict[str, Any]] = None


class LlmAxisScoresResponse(BaseModel):
    scores: list[LlmAxisScore]


async def query_pipeline(payload: dict) -> dict:
    """Query pipeline API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json=payload
        )
        response.raise_for_status()
        return response.json()


async def signal_scores_endpoint(
    signal_ids: Optional[list[str]] = None,
    category: Optional[str] = None,
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Get signal scores from pipeline."""
    payload: dict[str, Any] = {"query": "signal_scores"}
    if signal_ids:
        payload["signal_ids"] = signal_ids
    if category:
        payload["category"] = category
    data = await query_pipeline(payload)
    return SignalScoresResponse(
        scores=[SignalScore(**s) for s in data.get("data", [])],
        total=data.get("total", 0)
    )


async def get_signal_scores(
    signal_ids: list[str],
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Get signal scores for specific signal IDs."""
    payload = {"query": "signal_scores", "signal_ids": signal_ids}
    data = await query_pipeline(payload)
    return SignalScoresResponse(
        scores=[SignalScore(**s) for s in data.get("data", [])],
        total=data.get("total", 0)
    )


async def mesh_scores_endpoint(
    keys: Optional[list[str]] = None,
    category: Optional[str] = None,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Get mesh memory from pipeline."""
    payload: dict[str, Any] = {"query": "mesh_memory"}
    if keys:
        payload["keys"] = keys
    if category:
        payload["category"] = category
    data = await query_pipeline(payload)
    return MeshMemoryResponse(
        entries=[MeshMemoryEntry(**e) for e in data.get("data", [])],
        count=data.get("count", 0)
    )


async def get_mesh_memory(
    keys: list[str],
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Get mesh memory for specific keys."""
    payload = {"query": "mesh_memory", "keys": keys}
    data = await query_pipeline(payload)
    return MeshMemoryResponse(
        entries=[MeshMemoryEntry(**e) for e in data.get("data", [])],
        count=data.get("count", 0)
    )


async def llm_axis_scores_endpoint(
    axes: Optional[list[str]] = None,
    session: Session = Depends(get_session),
) -> LlmAxisScoresResponse:
    """Get LLM axis scores from pipeline."""
    payload: dict[str, Any] = {"query": "llm_axis_scores"}
    if axes:
        payload["axes"] = axes
    data = await query_pipeline(payload)
    return LlmAxisScoresResponse(
        scores=[LlmAxisScore(**s) for s in data.get("data", [])]
    )


async def get_mesh_memory_endpoint(
    keys: Optional[list[str]] = None,
    category: Optional[str] = None,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Get mesh memory endpoint."""
    return await mesh_scores_endpoint(keys=keys, category=category)


def _run_self_test():
    """Run self-test for this module."""
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    local_app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session
    print("PASS")


if __name__ == "__main__":
    _run_self_test()