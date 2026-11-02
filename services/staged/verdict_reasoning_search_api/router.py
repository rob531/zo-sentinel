"""HTTP surface for verdict reasoning search."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import search_verdict_reasoning

router = APIRouter(prefix="/api", tags=["verdict-reasoning-search"])


class ReasoningSearchResult(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    snippet: str
    last_assessed: Optional[datetime] = None


class ReasoningSearchResponse(BaseModel):
    q: str
    count: int
    results: list[ReasoningSearchResult]


@router.get(
    "/servers/reasoning-search",
    response_model=ReasoningSearchResponse,
)
def reasoning_search(
    q: str = Query("", description="Text to find in server names or verdict reasoning"),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_session),
):
    if not q.strip():
        return JSONResponse(status_code=400, content={"error": "q_required"})

    results = search_verdict_reasoning(db, q, limit)
    return ReasoningSearchResponse(q=q, count=len(results), results=results)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="matching-server",
                    name="Matching Server",
                    url="https://matching.example",
                    verdict="compromised",
                    verdict_reasoning="The exposed administrative endpoint allows unauthorized access.",
                    risk_tier="critical",
                    trust_score=0.2,
                    last_assessed=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="non-matching-server",
                    name="Healthy Server",
                    url="https://healthy.example",
                    verdict="safe",
                    verdict_reasoning="All controls are operating normally.",
                    risk_tier="low",
                    trust_score=0.9,
                    last_assessed=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="null-reasoning-server",
                    name="Unknown Server",
                    url="https://unknown.example",
                    verdict="unknown",
                    verdict_reasoning=None,
                    risk_tier="medium",
                    trust_score=0.5,
                    last_assessed=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/servers/reasoning-search",
            params={"q": "exposed", "limit": 25},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 1, payload
        assert payload["results"][0]["server_id"] == "matching-server", payload
        assert "exposed" in payload["results"][0]["snippet"].lower(), payload

        response = client.get("/api/servers/reasoning-search", params={"q": ""})
        assert response.status_code == 400, response.text
        assert response.json() == {"error": "q_required"}, response.text

    print("PASS")