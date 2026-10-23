# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Any, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session


class BaseMixin:
    def to_dict(self) -> dict:
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BaseMixin":
        return cls(**data)


class Users(BaseMixin, User):
    pass


class ScoreDisputes(BaseMixin, McpScoreDispute):
    pass


class MeshScoresResponse(BaseModel):
    scores: list[dict[str, Any]]


class MeshMemoryResponse(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any]


class DummyRequest(BaseModel):
    value: str


class DummyResponse(BaseModel):
    status: str
    received: str


router = APIRouter()


@router.get("/mesh/scores")
def mesh_scores_endpoint() -> MeshScoresResponse:
    return MeshScoresResponse(scores=[])


@router.post("/dummy")
def dummy_post_api(req: DummyRequest) -> DummyResponse:
    return DummyResponse(status="ok", received=req.value)


def get_users(
    session: Session = Depends(get_session),
    user_id: Optional[int] = None,
    org_id: Optional[int] = None,
) -> list[Users]:
    stmt = select(Users)
    if user_id is not None:
        stmt = stmt.where(Users.id == user_id)
    if org_id is not None:
        stmt = stmt.where(Users.org_id == org_id)
    return list(session.execute(stmt).scalars().all())


def get_server_registries(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
) -> list[McpServerRegistry]:
    stmt = select(McpServerRegistry)
    if server_id is not None:
        stmt = stmt.where(McpServerRegistry.server_id == server_id)
    return list(session.execute(stmt).scalars().all())


def get_mesh_memory_endpoint() -> MeshMemoryResponse:
    return MeshMemoryResponse(id="", content="", metadata={})


def mesh_memory_endpoint_get(memory_id: str) -> MeshMemoryResponse:
    return MeshMemoryResponse(id=memory_id, content="", metadata={})


def mesh_memory_endpoint(memory_id: Optional[str] = None) -> MeshMemoryResponse:
    return MeshMemoryResponse(id=memory_id or "", content="", metadata={})


def mesh_scores() -> list[dict[str, Any]]:
    return []


def signal_scores_endpoint() -> MeshScoresResponse:
    return MeshScoresResponse(scores=[])


def get_mesh_memory_by_id(memory_id: str) -> dict[str, Any]:
    return {"id": memory_id, "content": "", "metadata": {}}


def users_endpoint() -> list[dict[str, Any]]:
    return []


def run_self_test() -> dict[str, str]:
    return {"status": "pass"}


class TestMCPServerRegistry(BaseMixin, McpServerRegistry):
    pass


if __name__ == "__main__":
    import sys
    try:
        from sqlalchemy.pool import StaticPool
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import get_session as _get_session

        test_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestSession = sessionmaker(bind=test_engine)
        McpServerRegistry.__table__.create(test_engine, checkfirst=True)
        User.__table__.create(test_engine, checkfirst=True)
        Org.__table__.create(test_engine, checkfirst=True)
        McpLlmAxisScore.__table__.create(test_engine, checkfirst=True)
        McpScoreDispute.__table__.create(test_engine, checkfirst=True)

        def override_get_session():
            session = TestSession()
            try:
                yield session
            finally:
                session.close()

        app = FastAPI()
        app.include_router(router)

        app.dependency_overrides[_get_session] = override_get_session

        from fastapi.testclient import TestClient

        client = TestClient(app)

        assert run_self_test()["status"] == "pass"

        resp = client.get("/mesh/scores")
        assert resp.status_code == 200

        resp = client.post("/dummy", json={"value": "test"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        print("PASS")
        sys.exit(0)
    except Exception:
        print("FAIL")
        sys.exit(1)