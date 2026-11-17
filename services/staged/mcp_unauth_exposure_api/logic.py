"""Logic for reporting unauthenticated-access exposure across MCP registries."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


class ExposureServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    unauth_access: Optional[str] = None
    url: Optional[str] = None
    updated_at: Optional[datetime] = None


class ExposureObservation(BaseModel):
    url: Optional[str] = None
    unauth_access: Optional[str] = None
    source: Optional[str] = None
    updated_at: Optional[datetime] = None


class SourceCount(BaseModel):
    source: str
    count: int


class StateCount(BaseModel):
    unauth_access: str
    count: int


class ExposureListResponse(BaseModel):
    total: int
    by_source: List[SourceCount]
    by_state: List[StateCount]
    servers: List[ExposureServer]


class ServerExposureResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    observations: List[ExposureObservation] = Field(default_factory=list)


def _source_counts(accesses: List[McpUnauthAccess]) -> List[SourceCount]:
    counts = Counter(access.source or "unknown" for access in accesses)
    return [
        SourceCount(source=source, count=int(count))
        for source, count in sorted(counts.items())
    ]


def _state_counts(accesses: List[McpUnauthAccess]) -> List[StateCount]:
    counts = Counter(access.unauth_access or "unknown" for access in accesses)
    return [
        StateCount(unauth_access=state, count=int(count))
        for state, count in sorted(counts.items())
    ]


def get_exposure(db: Session) -> ExposureListResponse:
    """Return all unauthenticated-access observations and their aggregates."""
    rows = db.execute(
        select(McpUnauthAccess, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .order_by(McpUnauthAccess.server_id)
    ).all()

    accesses = [row[0] for row in rows]
    servers = [
        ExposureServer(
            server_id=str(access.server_id),
            name=registry.name if registry is not None else None,
            registry_source=registry.registry_source if registry is not None else None,
            risk_tier=registry.risk_tier if registry is not None else None,
            unauth_access=access.unauth_access,
            url=access.url,
            updated_at=access.updated_at,
        )
        for access, registry in rows
    ]

    return ExposureListResponse(
        total=len(accesses),
        by_source=_source_counts(accesses),
        by_state=_state_counts(accesses),
        servers=servers,
    )


def get_server_exposure(db: Session, server_id: str) -> ServerExposureResponse:
    """Return the registry metadata and access observations for one server."""
    rows = db.execute(
        select(McpUnauthAccess, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .where(McpUnauthAccess.server_id == server_id)
        .order_by(McpUnauthAccess.updated_at, McpUnauthAccess.source)
    ).all()

    registry = next((row[1] for row in rows if row[1] is not None), None)
    if registry is None:
        registry = db.get(McpServerRegistry, server_id)

    if not rows and registry is None:
        raise HTTPException(status_code=404, detail="Server not found")

    observations = [
        ExposureObservation(
            url=access.url,
            unauth_access=access.unauth_access,
            source=access.source,
            updated_at=access.updated_at,
        )
        for access, _ in rows
    ]

    return ServerExposureResponse(
        server_id=server_id,
        name=registry.name if registry is not None else None,
        registry_source=registry.registry_source if registry is not None else None,
        risk_tier=registry.risk_tier if registry is not None else None,
        observations=observations,
    )


router = APIRouter(prefix="/api/mcp/unauth", tags=["mcp-unauth-exposure"])


@router.get("/exposure", response_model=ExposureListResponse)
def list_exposure(
    db: Session = Depends(get_session),
) -> ExposureListResponse:
    return get_exposure(db)


@router.get("/exposure/{server_id}", response_model=ServerExposureResponse)
def server_exposure(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerExposureResponse:
    return get_server_exposure(db, server_id)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpUnauthAccess.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestingSession() as db:
        db.add_all(
            [
                McpUnauthAccess(
                    server_id="mcp-001",
                    url="https://one.example/mcp",
                    unauth_access="allowed",
                    source="registry-a",
                ),
                McpUnauthAccess(
                    server_id="mcp-002",
                    url="https://two.example/mcp",
                    unauth_access="blocked",
                    source="registry-a",
                ),
                McpUnauthAccess(
                    server_id="mcp-003",
                    url="https://three.example/mcp",
                    unauth_access="allowed",
                    source="registry-b",
                ),
                McpServerRegistry(
                    server_id="mcp-001",
                    name="One",
                    registry_source="registry-a",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="mcp-002",
                    name="Two",
                    registry_source="registry-a",
                    risk_tier="medium",
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/mcp/unauth/exposure")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert len(payload["by_source"]) == 2
        assert {"source": "registry-a", "count": 2} in payload["by_source"]
        assert len(payload["servers"]) == 3
        assert payload["servers"][0]["server_id"] == "mcp-001"
        assert payload["servers"][0]["name"] == "One"

        response = client.get("/api/mcp/unauth/exposure/mcp-001")
        assert response.status_code == 200
        payload = response.json()
        assert payload["server_id"] == "mcp-001"
        assert len(payload["observations"]) == 1
        assert payload["observations"][0]["source"] == "registry-a"

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")