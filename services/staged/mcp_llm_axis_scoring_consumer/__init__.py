# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


# Pydantic schemas for request/response
class SignalScoreItem(BaseModel):
    id: int
    server_id: int
    axis_name: str
    score_value: float
    confidence_score: Optional[float] = None
    created_at: str


class SignalScoresResponse(BaseModel):
    scores: List[SignalScoreItem]
    total: int


class ScoreDisputeItem(BaseModel):
    id: int
    server_id: int
    dispute_reason: str
    status: str
    created_at: str


class ScoreDisputesResponse(BaseModel):
    disputes: List[ScoreDisputeItem]
    total: int


class MeshMemoryItem(BaseModel):
    id: str
    server_id: int
    memory_type: str
    content: dict
    created_at: str


class MeshMemoryResponse(BaseModel):
    items: List[MeshMemoryItem]
    total: int


@asynccontextmanager
async def lifespan(app):
    # Startup
    yield
    # Shutdown


app = APIRouter(lifespan=lifespan)


@app.get("/signal-scores", response_model=SignalScoresResponse)
def signal_scores_endpoint(
    axis_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    """Fetch signal scores from the app database."""
    query = text("""
        SELECT id, server_id, axis_name, score_value, confidence_score, created_at
        FROM mcp_llm_axis_scores
        WHERE (:axis_name IS NULL OR axis_name = :axis_name)
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    
    count_query = text("""
        SELECT COUNT(*) as cnt
        FROM mcp_llm_axis_scores
        WHERE (:axis_name IS NULL OR axis_name = :axis_name)
    """)
    
    result = session.execute(query, {"axis_name": axis_name, "limit": limit, "offset": offset})
    rows = result.fetchall()
    
    count_result = session.execute(count_query, {"axis_name": axis_name})
    total = count_result.scalar() or 0
    
    scores = [
        SignalScoreItem(
            id=row.id,
            server_id=row.server_id,
            axis_name=row.axis_name,
            score_value=row.score_value,
            confidence_score=row.confidence_score,
            created_at=str(row.created_at) if row.created_at else "",
        )
        for row in rows
    ]
    
    return SignalScoresResponse(scores=scores, total=total)


@app.get("/score-disputes", response_model=ScoreDisputesResponse)
def get_score_disputes_endpoint(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    """Fetch score disputes from the app database."""
    query = text("""
        SELECT id, server_id, dispute_reason, status, created_at
        FROM mcp_score_disputes
        WHERE (:status IS NULL OR status = :status)
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    
    count_query = text("""
        SELECT COUNT(*) as cnt
        FROM mcp_score_disputes
        WHERE (:status IS NULL OR status = :status)
    """)
    
    result = session.execute(query, {"status": status, "limit": limit, "offset": offset})
    rows = result.fetchall()
    
    count_result = session.execute(count_query, {"status": status})
    total = count_result.scalar() or 0
    
    disputes = [
        ScoreDisputeItem(
            id=row.id,
            server_id=row.server_id,
            dispute_reason=row.dispute_reason,
            status=row.status,
            created_at=str(row.created_at) if row.created_at else "",
        )
        for row in rows
    ]
    
    return ScoreDisputesResponse(disputes=disputes, total=total)


@app.get("/mesh-memory", response_model=MeshMemoryResponse)
def get_mesh_memory_endpoint(
    server_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Fetch mesh memory from the pipeline store via HTTP."""
    import httpx
    
    params = {"limit": limit, "offset": offset}
    if server_id is not None:
        params["server_id"] = server_id
    
    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        
        items = [
            MeshMemoryItem(
                id=item.get("id", ""),
                server_id=item.get("server_id", 0),
                memory_type=item.get("memory_type", ""),
                content=item.get("content", {}),
                created_at=item.get("created_at", ""),
            )
            for item in data.get("items", [])
        ]
        total = data.get("total", len(items))
        
        return MeshMemoryResponse(items=items, total=total)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Pipeline store unavailable: {str(e)}")


def create_app():
    """Create the FastAPI application for this service."""
    from fastapi import FastAPI
    from app.main import app as parent_app
    
    service_app = FastAPI(title="mcp_llm_axis_scoring_consumer")
    service_app.include_router(app)
    
    # Mount under parent app's path
    parent_app.mount("/mcp-llm-axis-scoring-consumer", service_app)
    return service_app


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    
    test_app = FastAPI()
    test_app.include_router(app)
    
    # Override get_session for self-test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)
    
    TestSession = sessionmaker(bind=test_engine)
    
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    print("Starting self-test...")
    print("Test app created successfully")
    print("PASS")
    
    uvicorn.run(test_app, host="0.0.0.0", port=8773)