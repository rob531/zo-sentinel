from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List

from app.db import get_session
from .logic import get_registry_growth_snapshots

router = APIRouter(prefix="/api")


class SnapshotSource(BaseModel):
    name: str = Field(..., description="Source name")
    count: int = Field(..., description="Count for this source")


class Snapshot(BaseModel):
    date: str = Field(..., description="ISO date string")
    count: int = Field(..., description="Total count for the day")
    sources: List[SnapshotSource] = Field(..., description="Breakdown by source")


class SnapshotsResponse(BaseModel):
    snapshots: List[Snapshot] = Field(..., description="Growth snapshots")


@router.get(
    "/registry/growth/snapshots",
    response_model=SnapshotsResponse,
    summary="Get daily registry growth snapshots",
)
async def registry_growth_snapshots(session=Depends(get_session)):
    """
    Returns daily growth snapshots for the server registry.
    """
    snapshots = await get_registry_growth_snapshots(session)
    return SnapshotsResponse(snapshots=snapshots)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, McpServerRegistry  # type: ignore

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and seed it with two days of data
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    with SessionLocal() as sess:
        sess.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    source_name="source-A",
                    registered_at=datetime.datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    source_name="source-B",
                    registered_at=datetime.datetime.utcnow()
                    - datetime.timedelta(days=1),
                ),
            ]
        )
        sess.commit()

    # ------------------------------------------------------------------- #
    # Dependency override to use the SQLite session
    # ------------------------------------------------------------------- #
    async def get_test_session():
        with SessionLocal() as sess:
            yield sess

    # ------------------------------------------------------------------- #
    # Build FastAPI app, include router, and run test client
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)
    resp = client.get("/api/registry/growth/snapshots")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert isinstance(payload.get("snapshots"), list) and len(payload["snapshots"]) > 0
    print("PASS")