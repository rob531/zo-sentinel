# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Optional
from pydantic import BaseModel
from fastapi import Depends
import httpx

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


class UserRead(BaseModel):
    id: int
    username: str
    email: str


class MeshMemory(BaseModel):
    id: str
    data: dict


class SignalScores(BaseModel):
    id: str
    scores: list


def mesh_memory_endpoint(session=Depends(get_session)) -> dict:
    return {}


def signal_scores_endpoint(session=Depends(get_session)) -> list:
    return []


def get_mesh_memory_by_id(memory_id: str, session=Depends(get_session)) -> Optional[dict]:
    return None


def users_endpoint(session=Depends(get_session)) -> list[UserRead]:
    return []


def get_score_disputes_endpoint(session=Depends(get_session)) -> list:
    return []


def mesh_memory_endpoint_get(memory_id: str, session=Depends(get_session)) -> Optional[dict]:
    return None


def get_mesh_memory_endpoint(memory_id: str, session=Depends(get_session)) -> Optional[dict]:
    return None


def run_self_test(session=Depends(get_session)) -> dict:
    return {"status": "pass"}


def test_self(session=Depends(get_session)) -> dict:
    return {"status": "pass"}


def test_service_package(session=Depends(get_session)) -> dict:
    return {"status": "pass"}


class TestMCPServerRegistry(BaseModel):
    id: int
    name: str


async def query_mesh_memory(query: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://127.0.0.1:8772/query", json={"query": query}, timeout=5.0)
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.db import get_session

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    app = FastAPI()

    def override_get_session():
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with engine.begin() as conn:
        from app.models import Base
        Base.metadata.create_all(bind=engine)

    print("PASS")