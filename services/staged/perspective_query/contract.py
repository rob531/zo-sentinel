import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Any
from collections.abc import AsyncIterator

from fastapi import FastAPI, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


class PerspectiveResponse(BaseModel):
    id: int
    org_id: int
    name: str
    description: Optional[str] = None
    facet_filters: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    latest_snapshot_at: Optional[datetime] = None
    snapshot_count: int = 0

    class Config:
        from_attributes = True


class PerspectiveListResponse(BaseModel):
    items: List[PerspectiveResponse]
    total: int
    limit: int
    offset: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def build_service_unit() -> FastAPI:
    app = FastAPI(title="perspective_query_api", lifespan=lifespan)

    @app.get("/api/perspectives", response_model=PerspectiveListResponse)
    def list_perspectives(
        org_id: Optional[int] = Query(None, description="Filter by organization ID"),
        name: Optional[str] = Query(None, description="Filter by perspective name"),
        created_by: Optional[int] = Query(None, description="Filter by creator user ID"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum results to return"),
        offset: int = Query(0, ge=0, description="Number of results to skip"),
        session: Session = Depends(get_session)
    ) -> PerspectiveListResponse:
        base_query = select(Perspective)
        if org_id is not None:
            base_query = base_query.where(Perspective.org_id == org_id)
        if name is not None:
            base_query = base_query.where(Perspective.name == name)
        if created_by is not None:
            base_query = base_query.where(Perspective.created_by == created_by)

        total = session.execute(
            select(func.count()).select_from(base_query.subquery())
        ).scalar() or 0

        snapshot_counts = (
            select(
                PerspectiveSnapshot.perspective_id,
                func.count(PerspectiveSnapshot.id).label("snapshot_count"),
                func.max(PerspectiveSnapshot.taken_at).label("latest_snapshot_at")
            )
            .group_by(PerspectiveSnapshot.perspective_id)
            .subquery()
        )

        query = (
            select(Perspective, snapshot_counts.c.snapshot_count, snapshot_counts.c.latest_snapshot_at)
            .outerjoin(snapshot_counts, Perspective.id == snapshot_counts.c.perspective_id)
            .order_by(Perspective.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        rows = session.execute(query).all()

        items = []
        for row in rows:
            perspective = row[0]
            snapshot_count = row[1] or 0
            latest_snapshot_at = row[2]
            items.append(PerspectiveResponse(
                id=perspective.id,
                org_id=perspective.org_id,
                name=perspective.name,
                description=perspective.description,
                facet_filters=perspective.facet_filters,
                created_by=perspective.created_by,
                created_at=perspective.created_at,
                updated_at=perspective.updated_at,
                latest_snapshot_at=latest_snapshot_at,
                snapshot_count=snapshot_count
            ))

        return PerspectiveListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset
        )

    return app


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import timedelta
    import random

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    Perspective.metadata.create_all(test_engine)
    PerspectiveSnapshot.metadata.create_all(test_engine)

    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    org_ids = [1, 2, 3]
    now = datetime.utcnow()

    for i, org_id in enumerate(org_ids):
        perspective = Perspective(
            id=i + 1,
            org_id=org_id,
            name=f"Test Perspective {i+1}",
            description=f"Test description {i+1}",
            facet_filters='{"key": "value"}',
            created_by=100 + i,
            created_at=now - timedelta(days=i),
            updated_at=now - timedelta(days=i)
        )
        test_session.add(perspective)
        test_session.flush()

        num_snapshots = random.randint(1, 3)
        for j in range(num_snapshots):
            snapshot = PerspectiveSnapshot(
                perspective_id=perspective.id,
                taken_at=now - timedelta(hours=j),
                membership=j * 10
            )
            test_session.add(snapshot)

    test_session.commit()

    that_app = build_service_unit()
    that_app.dependency_overrides[get_session] = lambda: test_session

    from fastapi.testclient import TestClient
    client = TestClient(that_app)

    response = client.get("/api/perspectives")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["total"] == 3, f"Expected total==3, got {data['total']}"

    response = client.get("/api/perspectives?org_id=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1, f"Expected org_id filter to reduce count, got {data['total']}"
    assert data["items"][0]["org_id"] == 1

    response = client.get("/api/perspectives")
    data = response.json()
    for item in data["items"]:
        assert "snapshot_count" in item
        assert "latest_snapshot_at" in item

    print("PASS")
    sys.exit(0)