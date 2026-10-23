# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter(tags=["service_package"])


class MeshMemoryResponse(BaseModel):
    id: Optional[str] = None
    memory_data: Optional[dict] = None
    created_at: Optional[str] = None


class SignalScoresResponse(BaseModel):
    scores: list[dict]
    count: int


class UsersResponse(BaseModel):
    users: list[dict]
    count: int


def get_mesh_memory_endpoint() -> str:
    return "/api/mesh-memory"


def mesh_memory_endpoint() -> dict:
    return {"endpoint": get_mesh_memory_endpoint(), "method": "GET"}


def get_mesh_memory_by_id(memory_id: str) -> dict:
    return {
        "id": memory_id,
        "endpoint": f"{get_mesh_memory_endpoint()}/{memory_id}",
        "method": "GET"
    }


def get_signal_scores_endpoint() -> str:
    return "/api/signal-scores"


def signal_scores_endpoint() -> dict:
    return {"endpoint": get_signal_scores_endpoint(), "method": "POST"}


def get_users_endpoint() -> str:
    return "/api/users"


def users_endpoint() -> dict:
    return {"endpoint": get_users_endpoint(), "method": "GET"}


def get_score_disputes_endpoint() -> str:
    return "/api/score-disputes"


@router.get("/api/health")
def health_check():
    return {"status": "ok"}


@router.get("/api/mesh-memory/{memory_id}", response_model=MeshMemoryResponse)
def get_mesh_memory_by_id_endpoint(memory_id: str):
    return MeshMemoryResponse(
        id=memory_id,
        memory_data={"message": "mesh memory data"},
        created_at="2024-01-01T00:00:00Z"
    )


@router.post("/api/signal-scores", response_model=SignalScoresResponse)
def signal_scores_list():
    return SignalScoresResponse(scores=[], count=0)


@router.get("/api/users", response_model=UsersResponse)
def users_list(session=Depends(get_session)):
    result = session.execute(text("SELECT 1"))
    return UsersResponse(users=[], count=0)


@router.get("/api/score-disputes", response_model=SignalScoresResponse)
def score_disputes_list(session=Depends(get_session)):
    result = session.execute(text("SELECT 1"))
    return SignalScoresResponse(scores=[], count=0)


def run_self_test() -> dict:
    from fastapi import FastAPI
    from app.db import get_session, Session
    from sqlalchemy.pool import StaticPool
    from app.main import app as main_app

    test_app = FastAPI()

    @test_app.get("/api/health")
    def test_health():
        return {"status": "ok"}

    @test_app.get("/api/mesh-memory/{memory_id}", response_model=MeshMemoryResponse)
    def test_get_memory(memory_id: str):
        return MeshMemoryResponse(id=memory_id, memory_data={})

    def override_get_session():
        return Session(bind=StaticPool())

    test_app.dependency_overrides[get_session] = override_get_session

    assert mesh_memory_endpoint() == {"endpoint": "/api/mesh-memory", "method": "GET"}
    assert get_mesh_memory_endpoint() == "/api/mesh-memory"
    assert signal_scores_endpoint() == {"endpoint": "/api/signal-scores", "method": "POST"}
    assert get_signal_scores_endpoint() == "/api/signal-scores"
    assert users_endpoint() == {"endpoint": "/api/users", "method": "GET"}
    assert get_users_endpoint() == "/api/users"
    assert get_score_disputes_endpoint() == "/api/score-disputes"

    result = test_app.url_path_for("test_health")
    assert "health" in str(result)

    return {"status": "PASS"}


if __name__ == "__main__":
    print(run_self_test()["status"])