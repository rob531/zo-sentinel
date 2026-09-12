"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class SignalScore(BaseModel):
    signal: str
    score: float
    timestamp: str


class SignalScoresResponse(BaseModel):
    axis: str
    scores: List[SignalScore]


def signal_scores_endpoint(
    axis: str = Query(..., description="The axis name (e.g., 'llm', 'reliability')"),
    signal: Optional[str] = Query(None, description="Optional signal filter"),
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Fetch signal scores from the mesh pipeline store."""
    query_payload: Dict[str, Any] = {
        "query": {
            "sql": "SELECT signal, score, timestamp FROM mcp_signal_scores WHERE axis = :axis",
            "params": {"axis": axis},
        }
    }
    if signal:
        query_payload["query"]["sql"] += " AND signal = :signal"
        query_payload["query"]["params"]["signal"] = signal

    try:
        import requests

        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query_payload,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
    except requests.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Mesh query failed: {e}")

    scores = [
        SignalScore(signal=row["signal"], score=row["score"], timestamp=row["timestamp"])
        for row in rows
    ]
    return SignalScoresResponse(axis=axis, scores=scores)


@router.get("/signals", response_model=SignalScoresResponse)
def get_signal_scores(
    axis: str = Query("llm"),
    signal: Optional[str] = None,
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    return signal_scores_endpoint(axis=axis, signal=signal, session=session)


if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with test_engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS mcp_signal_scores (signal TEXT, score REAL, timestamp TEXT, axis TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO mcp_signal_scores (signal, score, timestamp, axis) VALUES ('quality', 0.95, '2024-01-01T00:00:00Z', 'llm')"
            )
        )
        conn.commit()

    print("Starting self-test server on :18772...")
    uvicorn.run(app, host="127.0.0.1", port=18772, log_level="error")