from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from sqlalchemy import select

router = APIRouter()


class LLMAxisScores(BaseModel):
    pass


class ScoreDisputeResponse(BaseModel):
    id: int
    confidence: float
    status: str

    class Config:
        from_attributes = True


class MeshMemoryResponse(BaseModel):
    id: int
    content: str

    class Config:
        from_attributes = True


class SignalScoreResponse(BaseModel):
    server_id: int
    score: float

    class Config:
        from_attributes = True


class AxisScoreResponse(BaseModel):
    axis: str
    score: float

    class Config:
        from_attributes = True


class SubmissionStatus(BaseModel):
    submission_id: int
    status: str


class ServerRegistryEntry(BaseModel):
    id: int
    name: str
    confidence: float

    class Config:
        from_attributes = True


@router.get("/api/score-disputes")
async def get_score_disputes(session=Depends(get_session)):
    result = session.execute(select(McpScoreDispute).limit(100))
    disputes = result.scalars().all()
    return [{"id": d.id, "confidence": d.confidence, "status": getattr(d, 'status', 'pending')} for d in disputes]


@router.get("/api/users")
async def users_endpoint(session=Depends(get_session)):
    from app.models import User
    result = session.execute(select(User).limit(100))
    users = result.scalars().all()
    return [{"id": u.id, "username": getattr(u, 'username', 'unknown')} for u in users]


@router.get("/api/admin/disputes/scores")
async def get_mcp_llm_axis_scores(session=Depends(get_session)):
    result = session.execute(select(McpLlmAxisScore).limit(100))
    scores = result.scalars().all()
    return [{"axis": getattr(s, 'axis', 'unknown'), "score": s.confidence} for s in scores]


@router.get("/api/mesh-memory")
async def mesh_memory_endpoint(session=Depends(get_session)):
    return {"status": "ok", "entries": []}


@router.get("/api/mesh-memory/by-id/{memory_id}")
async def get_mesh_memory(memory_id: int, session=Depends(get_session)):
    return {"id": memory_id, "content": ""}


@router.get("/api/authority/log-report")
async def mesh_memory_endpoint_get(session=Depends(get_session)):
    return {"status": "ok"}


@router.get("/api/axis/attribution")
async def api_mesh_memory(session=Depends(get_session)):
    return {"attributions": []}


@router.get("/api/axis/escalation-timeline")
async def axis_escalation_timeline_endpoint(session=Depends(get_session)):
    return {"timeline": []}


@router.get("/api/mesh/scores")
async def mesh_scores_endpoint(session=Depends(get_session)):
    return {"scores": []}


@router.get("/api/signal/scores")
async def api_signal_scores(session=Depends(get_session)):
    return {"signal_scores": []}


@router.get("/api/signal/scores/freshness")
async def signal_scores_endpoint(session=Depends(get_session)):
    return {"freshness": []}


@router.get("/api/signal/scores/freshness/consume")
async def get_mcp_llm_axis_scores_fresh(session=Depends(get_session)):
    return {"axis_scores": []}


@router.get("/api/axis/summary")
async def axis_summary_endpoint(session=Depends(get_session)):
    return {"summary": []}


@router.get("/api/axis/scores/to-verdict")
async def mesh_scores_verdict_endpoint(session=Depends(get_session)):
    return {"verdicts": []}


@router.get("/api/axis/scores")
async def get_axis_scores(session=Depends(get_session)):
    return {"axis_scores": []}


@router.get("/api/axis/scores/by-id/{score_id}")
async def get_mesh_memory_by_id(score_id: int, session=Depends(get_session)):
    return {"id": score_id, "content": ""}


@router.get("/api/submissions/view")
async def submissions_view(session=Depends(get_session)):
    return {"submissions": []}


@router.get("/api/advisory/alerts")
async def advisory_alert(session=Depends(get_session)):
    return {"alerts": []}


@router.get("/api/attestation/summary")
async def attestation_summary(session=Depends(get_session)):
    return {"summary": []}


@router.get("/api/axis/direction/consume")
async def axis_direction_consume(session=Depends(get_session)):
    return {"status": "ok"}


@router.get("/api/axis/volatility/consume")
async def axis_volatility_consume(session=Depends(get_session)):
    return {"status": "ok"}


@router.get("/api/axis/probability/summary")
async def axis_probability_summary(session=Depends(get_session)):
    return {"probability_summary": []}


@router.get("/api/axis/probability/variance/consume")
async def axis_probability_variance_consume(session=Depends(get_session)):
    return {"status": "ok"}


@router.get("/api/axis/time-series")
async def axis_time_series(session=Depends(get_session)):
    return {"time_series": []}


@router.get("/health")
async def health():
    return {"status": "healthy"}


def _run_self_test():
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200, f"Health check failed: {response.status_code}"
    assert response.json() == {"status": "healthy"}, f"Unexpected health response: {response.json()}"

    disputes_response = client.get("/api/score-disputes")
    assert disputes_response.status_code == 200, f"Score disputes endpoint failed: {disputes_response.status_code}"

    users_response = client.get("/api/users")
    assert users_response.status_code == 200, f"Users endpoint failed: {users_response.status_code}"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()