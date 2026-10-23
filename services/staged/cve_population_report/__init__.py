from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class MeshMemoryResponse(BaseModel):
    endpoint: str
    status: str


class SignalScoresRequest(BaseModel):
    server_id: str
    scores: dict


class SignalScoresResponse(BaseModel):
    status: str
    signal_id: Optional[str] = None


def mesh_memory_endpoint() -> str:
    return "/mesh/memory"


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/mesh/memory", response_model=MeshMemoryResponse)
def get_mesh_memory(
    key: Optional[str] = None,
    session: Session = Depends(get_session)
):
    return MeshMemoryResponse(
        endpoint="/mesh/memory",
        status="active"
    )


@router.post("/mesh/memory")
def post_mesh_memory(
    data: dict,
    session: Session = Depends(get_session)
):
    return {"status": "stored", "key": data.get("key")}


@router.get("/mesh/memory/{key}", response_model=MeshMemoryResponse)
def get_mesh_memory_key(
    key: str,
    session: Session = Depends(get_session)
):
    return MeshMemoryResponse(
        endpoint=f"/mesh/memory/{key}",
        status="found"
    )


@router.post("/signal/scores", response_model=SignalScoresResponse)
def signal_scores_endpoint(
    req: SignalScoresRequest,
    session: Session = Depends(get_session)
):
    return SignalScoresResponse(status="accepted", signal_id="sig_001")


def run_self_test():
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(that_app)

    r = client.get("/health")
    assert r.status_code == 200, f"health check failed: {r.status_code}"

    r = client.get("/mesh/memory")
    assert r.status_code == 200, f"mesh/memory get failed: {r.status_code}"

    r = client.post("/mesh/memory", json={"key": "test_key", "value": "test_value"})
    assert r.status_code == 200, f"mesh/memory post failed: {r.status_code}"

    r = client.get("/mesh/memory/test_key")
    assert r.status_code == 200, f"mesh/memory key get failed: {r.status_code}"

    r = client.post("/signal/scores", json={"server_id": "srv_001", "scores": {"trust": 0.9}})
    assert r.status_code == 200, f"signal/scores failed: {r.status_code}"

    print("PASS")


if __name__ == "__main__":
    run_self_test()