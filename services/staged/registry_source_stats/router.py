"""Thin API router for per-registry source statistics."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_source_stats

router = APIRouter(prefix="/api", tags=["registry_source_stats"])


class RegistrySourceStat(BaseModel):
    registry_source: str | None = None
    server_count: int
    last_seen: datetime | None = None
    first_seen: datetime | None = None


class RegistrySourcesResponse(BaseModel):
    sources: list[RegistrySourceStat]


@router.get("/registry/sources", response_model=RegistrySourcesResponse)
def get_registry_sources(db: Session = Depends(get_session)) -> RegistrySourcesResponse:
    return RegistrySourcesResponse(sources=get_registry_source_stats(db))


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpServerRegistry

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session():
        with TestSessionLocal() as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    first = datetime(2024, 1, 1, tzinfo=timezone.utc)
    second = datetime(2024, 1, 2, tzinfo=timezone.utc)
    third = datetime(2024, 1, 3, tzinfo=timezone.utc)
    with TestSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    name="Server One",
                    registry_source="source_a",
                    first_seen=first,
                    last_seen=second,
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    name="Server Two",
                    registry_source="source_a",
                    first_seen=second,
                    last_seen=third,
                ),
                McpServerRegistry(
                    server_id="srv-3",
                    name="Server Three",
                    registry_source="source_b",
                    first_seen=first,
                    last_seen=third,
                ),
            ]
        )
        db.commit()
        stats = get_registry_source_stats(db)

    counts = {row["registry_source"]: row["server_count"] for row in stats}
    assert counts == {"source_a": 2, "source_b": 1}, counts

    with TestClient(test_app) as client:
        response = client.get("/api/registry/sources")
    assert response.status_code == 200, response.text
    payload = response.json()
    response_counts = {
        row["registry_source"]: row["server_count"] for row in payload["sources"]
    }
    assert response_counts == counts, response_counts
    assert all(
        set(row) == {"registry_source", "server_count", "last_seen", "first_seen"}
        for row in payload["sources"]
    )
    print("PASS")