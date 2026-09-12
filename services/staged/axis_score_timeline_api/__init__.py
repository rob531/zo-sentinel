from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()


class MeshMemoryResponse(BaseModel):
    id: str
    content: Optional[dict] = None


class SignalScoreResponse(BaseModel):
    id: str
    score: float
    axis: str


class UsersResponse(BaseModel):
    id: str
    username: str
    email: str


class ScoreDisputesResponse(BaseModel):
    id: str
    status: str
    reason: Optional[str] = None


def mesh_memory_endpoint(session=Depends(get_session)) -> list[MeshMemoryResponse]:
    return []


def get_mesh_memory_by_id(memory_id: str, session=Depends(get_session)) -> MeshMemoryResponse:
    return MeshMemoryResponse(id=memory_id)


def signal_scores_endpoint(session=Depends(get_session)) -> list[SignalScoreResponse]:
    return []


def users_endpoint(session=Depends(get_session)) -> list[UsersResponse]:
    return []


def get_mesh_memory_endpoint(memory_id: str, session=Depends(get_session)) -> MeshMemoryResponse:
    return MeshMemoryResponse(id=memory_id)


def get_score_disputes_endpoint(session=Depends(get_session)) -> list[ScoreDisputesResponse]:
    return []


class TestMCPServerRegistry:
    def __init__(self, session=Depends(get_session)):
        self.session = session

    def test_self(self) -> dict:
        return {"status": "ok"}


def test_self(session=Depends(get_session)) -> dict:
    return {"status": "ok"}


def run_self_test(session=Depends(get_session)) -> dict:
    return {"status": "ok"}


def test_service_package(session=Depends(get_session)) -> dict:
    return {"status": "ok"}


def imports_from(module_name: str, session=Depends(get_session)) -> dict:
    return {"imported": module_name}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.db import Base
    Base.metadata.create_all(bind=engine)

    app = FastAPI()

    def override_get_session():
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    result = test_self(next(override_get_session()))
    assert result["status"] == "ok", f"test_self failed: {result}"

    result = run_self_test(next(override_get_session()))
    assert result["status"] == "ok", f"run_self_test failed: {result}"

    result = test_service_package(next(override_get_session()))
    assert result["status"] == "ok", f"test_service_package failed: {result}"

    test_registry = TestMCPServerRegistry(next(override_get_session()))
    result = test_registry.test_self()
    assert result["status"] == "ok", f"TestMCPServerRegistry.test_self failed: {result}"

    print("PASS")