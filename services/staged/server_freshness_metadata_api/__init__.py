from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpScoreDispute,
    Org,
    User,
)

router = APIRouter()


def mesh_scores_endpoint() -> Response:
    """Return mesh scores from ZoComputer store."""
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mcp_signal_scores", "action": "select", "filters": {}},
        timeout=10,
    )
    resp.raise_for_status()
    return Response(content=resp.text, media_type="application/json")


def get_mesh_memory_endpoint() -> Response:
    """Return mesh memory from ZoComputer store."""
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mesh_memory", "action": "select", "filters": {}},
        timeout=10,
    )
    resp.raise_for_status()
    return Response(content=resp.text, media_type="application/json")


def get_score_disputes_endpoint(
    session: Session = Depends(get_session),
) -> list[dict]:
    """Return score disputes from app database."""
    stmt = select(McpScoreDispute)
    results = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "created_at": str(r.created_at) if r.created_at else None,
        }
        for r in results
    ]


def signal_scores_endpoint(data: dict) -> Response:
    """Post signal scores to ZoComputer store."""
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mcp_signal_scores", "action": "upsert", "data": data},
        timeout=10,
    )
    resp.raise_for_status()
    return Response(content=resp.text, media_type="application/json")


def get_signal_scores(filters: Optional[dict] = None) -> Response:
    """Get signal scores from ZoComputer store."""
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "table": "mcp_signal_scores",
            "action": "select",
            "filters": filters or {},
        },
        timeout=10,
    )
    resp.raise_for_status()
    return Response(content=resp.text, media_type="application/json")


def get_mesh_memory(filters: Optional[dict] = None) -> Response:
    """Get mesh memory from ZoComputer store."""
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mesh_memory", "action": "select", "filters": filters or {}},
        timeout=10,
    )
    resp.raise_for_status()
    return Response(content=resp.text, media_type="application/json")


def mesh_scores() -> list[dict]:
    """Return mesh scores as structured data."""
    import requests

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mcp_signal_scores", "action": "select", "filters": {}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def orgs_endpoint(
    session: Session = Depends(get_session),
) -> list[dict]:
    """Return organizations from app database."""
    stmt = select(Org)
    results = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
        }
        for r in results
    ]


def update(data: dict, session: Session = Depends(get_session)) -> dict:
    """Update mesh data in ZoComputer store."""
    import requests

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"table": "mcp_signal_scores", "action": "upsert", "data": data},
        timeout=10,
    )
    resp.raise_for_status()
    return {"status": "updated"}


class OrgService:
    def __init__(self, session: Session):
        self.session = session

    def list_orgs(self) -> list[Org]:
        stmt = select(Org)
        return list(self.session.execute(stmt).scalars().all())

    def get_org(self, org_id: int) -> Optional[Org]:
        return self.session.get(Org, org_id)


class UserService:
    def __init__(self, session: Session):
        self.session = session

    def list_users(self) -> list[User]:
        stmt = select(User)
        return list(self.session.execute(stmt).scalars().all())

    def get_user(self, user_id: int) -> Optional[User]:
        return self.session.get(User, user_id)


if __name__ == "__main__":
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

    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/health")
    def health():
        return {"status": "ok"}

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=0)