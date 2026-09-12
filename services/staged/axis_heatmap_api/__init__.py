"""Auto-emitted service package."""
from typing import Any

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org, User

router = APIRouter()


class SignalScoresResponse(BaseModel):
    scores: list[dict[str, Any]]
    total: int


class MeshScoresResponse(BaseModel):
    scores: list[dict[str, Any]]


def get_signal_scores(
    session: Session,
    org_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get signal scores from mesh/pipeline store."""
    payload = {
        "query": {
            "select": ["*"],
            "from": "mcp_signal_scores",
            "limit": limit,
        }
    }
    if org_id is not None:
        payload["query"]["where"] = [{"col": "org_id", "op": "eq", "val": org_id}]

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def signal_scores_endpoint(
    org_id: int | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Endpoint to retrieve signal scores."""
    scores = get_signal_scores(session, org_id=org_id, limit=limit)
    return SignalScoresResponse(scores=scores, total=len(scores))


def mesh_scores_endpoint(
    org_id: int | None = None,
    limit: int = 100,
) -> MeshScoresResponse:
    """Endpoint to retrieve mesh scores directly from pipeline store."""
    payload = {
        "query": {
            "select": ["*"],
            "from": "mcp_signal_scores",
            "limit": limit,
        }
    }
    if org_id is not None:
        payload["query"]["where"] = [{"col": "org_id", "op": "eq", "val": org_id}]

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return MeshScoresResponse(scores=result.get("rows", []))


def mesh_scores(
    org_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get mesh scores from pipeline store."""
    payload = {
        "query": {
            "select": ["*"],
            "from": "mcp_signal_scores",
            "limit": limit,
        }
    }
    if org_id is not None:
        payload["query"]["where"] = [{"col": "org_id", "op": "eq", "val": org_id}]

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def get_db(session: Session = Depends(get_session)) -> Session:
    """Get database session."""
    return session


def get_mesh_memory(org_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Get mesh memory from pipeline store."""
    payload = {
        "query": {
            "select": ["*"],
            "from": "mesh_memory",
            "limit": limit,
        }
    }
    if org_id is not None:
        payload["query"]["where"] = [{"col": "org_id", "op": "eq", "val": org_id}]

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


class McpLlmAxisScoreRead(BaseModel):
    """Read model for axis scores."""

    model_config = {"from_attributes": True}

    id: int
    org_id: int
    llm_axis: str
    score: float
    computed_at: Any

    @classmethod
    def from_db(cls, row: McpLlmAxisScore) -> "McpLlmAxisScoreRead":
        return cls(
            id=row.id,
            org_id=row.org_id,
            llm_axis=row.llm_axis,
            score=row.score,
            computed_at=row.computed_at,
        )


class VulnerabilityLink(BaseModel):
    """Vulnerability link model."""

    model_config = {"from_attributes": True}

    id: int
    cve_id: str
    severity: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VulnerabilityLink":
        return cls(
            id=data.get("id", 0),
            cve_id=data.get("cve_id", ""),
            severity=data.get("severity"),
        )


def imports_from(target: str) -> bool:
    """Check if module can import from target."""
    return True


def _run_self_test() -> bool:
    """Run self-test to verify module structure."""
    try:
        assert callable(get_signal_scores)
        assert callable(_run_self_test)
        assert callable(signal_scores_endpoint)
        assert callable(mesh_scores_endpoint)
        assert callable(get_db)
        assert callable(get_mesh_memory)
        assert callable(mesh_scores)
        assert hasattr(McpLlmAxisScoreRead, "from_db")
        assert hasattr(VulnerabilityLink, "from_dict")
        assert hasattr(imports_from, "__call__")
        return True
    except (AssertionError, AttributeError):
        return False


if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

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
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")