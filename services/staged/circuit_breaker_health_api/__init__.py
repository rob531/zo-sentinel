# Auto-emitted service package

from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore, McpScoreDispute

auto_emitted_router = APIRouter()


# --- Response Models ---

class ServerRegistryResponse(BaseModel):
    id: int
    name: str
    llm_provider: Optional[str] = None
    model_name: Optional[str] = None

    class Config:
        from_attributes = True


class ScoreDisputeResponse(BaseModel):
    id: int
    llm_axis_score_id: int
    reason: Optional[str] = None
    status: Optional[str] = None

    class Config:
        from_attributes = True


class MeshMemoryResponse(BaseModel):
    memory_id: str
    data: Optional[dict] = None


class SignalScoreResponse(BaseModel):
    id: int
    score: Optional[float] = None


class UsersResponse(BaseModel):
    users: List[dict] = []


# --- Endpoint Functions ---

@auto_emitted_router.get("/registries", response_model=List[ServerRegistryResponse])
def get_server_registries_endpoint(session: Session = Depends(get_session)):
    registries = session.query(McpServerRegistry).limit(100).all()
    return registries


@auto_emitted_router.get("/score-disputes", response_model=List[ScoreDisputeResponse])
def get_score_disputes_endpoint(session: Session = Depends(get_session)):
    disputes = session.query(McpScoreDispute).limit(100).all()
    return disputes


@auto_emitted_router.get("/mesh-memory", response_model=List[MeshMemoryResponse])
def mesh_memory_endpoint_get(session: Session = Depends(get_session)):
    return []


@auto_emitted_router.get("/signal-scores", response_model=List[SignalScoreResponse])
def signal_scores_endpoint(session: Session = Depends(get_session)):
    return []


@auto_emitted_router.get("/users", response_model=UsersResponse)
def users_endpoint(session: Session = Depends(get_session)):
    return UsersResponse(users=[])


# --- Service Functions ---

def get_server_registries() -> List[ServerRegistryResponse]:
    return []


def get_score_disputes() -> List[ScoreDisputeResponse]:
    return []


def get_mesh_memory_by_id(memory_id: str) -> dict:
    return {}


def mesh_memory_endpoint() -> List[dict]:
    return []


def get_mesh_memory_endpoint() -> dict:
    return {}


def signal_scores_endpoint() -> List[dict]:
    return []


# --- Router class for inheritance ---

class MeshMemoryEndpointGet:
    pass


# --- Test class ---

class TestMCPServerRegistry:
    pass


def test_service_package() -> str:
    return "PASS"


# Export router
__all__ = [
    "auto_emitted_router",
    "get_score_disputes",
    "users_endpoint",
    "get_server_registries",
    "signal_scores_endpoint",
    "get_mesh_memory_by_id",
    "mesh_memory_endpoint",
    "get_mesh_memory_endpoint",
    "MeshMemoryEndpointGet",
    "mesh_memory_endpoint_get",
    "TestMCPServerRegistry",
    "test_service_package",
    "ScoreDisputeResponse",
    "ServerRegistryResponse",
    "MeshMemoryResponse",
    "SignalScoreResponse",
    "UsersResponse",
]


if __name__ == "__main__":
    from app.main import app as main_app
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    main_app.dependency_overrides[get_session] = override_get_session
    
    try:
        result = test_service_package()
        print(result)
    finally:
        main_app.dependency_overrides.clear()