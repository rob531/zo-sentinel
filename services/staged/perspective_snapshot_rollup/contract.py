import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import Perspective, PerspectiveSnapshot, Org

router = APIRouter(prefix="/api")


class PerspectiveSnapshotRollup(BaseModel):
    perspective_id: int = Field(..., description="Perspective identifier")
    org_id: int = Field(..., description="Organization identifier")
    name: str = Field(..., description="Perspective name")
    snapshot_count: int = Field(..., description="Number of snapshots")
    latest_snapshot_at: datetime.datetime = Field(
        ..., description="Timestamp of the most recent snapshot"
    )
    membership_sample: Optional[str] = Field(
        None, description="Membership field from the earliest snapshot"
    )

    class Config:
        orm_mode = True


@router.get(
    "/perspectives/{perspective_id}/snapshots/rollup",
    response_model=PerspectiveSnapshotRollup,
)
def get_perspective_snapshot_rollup(
    perspective_id: int, session: Session = Depends(get_session)
) -> PerspectiveSnapshotRollup:
    # Aggregate snapshot count and latest timestamp
    agg_stmt = (
        select(
            Perspective.id.label("perspective_id"),
            Perspective.org_id.label("org_id"),
            Perspective.name.label("name"),
            func.count(PerspectiveSnapshot.id).label("snapshot_count"),
            func.max(PerspectiveSnapshot.taken_at).label("latest_snapshot_at"),
        )
        .join(PerspectiveSnapshot, Perspective.id == PerspectiveSnapshot.perspective_id)
        .where(Perspective.id == perspective_id)
        .group_by(Perspective.id, Perspective.org_id, Perspective.name)
    )
    agg_res = session.execute(agg_stmt).first()
    if not agg_res:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Retrieve membership from the earliest snapshot
    earliest_stmt = (
        select(PerspectiveSnapshot.membership)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.asc())
        .limit(1)
    )
    membership = session.execute(earliest_stmt).scalar_one_or_none()

    return PerspectiveSnapshotRollup(
        perspective_id=agg_res.perspective_id,
        org_id=agg_res.org_id,
        name=agg_res.name,
        snapshot_count=agg_res.snapshot_count,
        latest_snapshot_at=agg_res.latest_snapshot_at,
        membership_sample=membership,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.perspective_snapshot_rollup.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables using the same metadata as the real models
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Session:
        with SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with SessionLocal() as s:
        org = Org(id=1, name="TestOrg", created_at=datetime.datetime.utcnow())
        s.add(org)

        perspectives = []
        for pid in (1, 2):
            p = Perspective(
                id=pid,
                name=f"Perspective {pid}",
                org_id=org.id,
                created_by=1,
                description="",
                facet_filters="",
                created_at=datetime.datetime.utcnow(),
                updated_at=datetime.datetime.utcnow(),
            )
            perspectives.append(p)
            s.add(p)

        now = datetime.datetime.utcnow()
        snapshots = []
        for p in perspectives:
            for i in range(3):
                snap = PerspectiveSnapshot(
                    id=len(snapshots) + 1,
                    perspective_id=p.id,
                    taken_at=now - datetime.timedelta(days=3 - i),
                    membership=f"member_{i}",
                )
                snapshots.append(snap)
                s.add(snap)

        s.commit()

    client = TestClient(app)

    for pid in (1, 2):
        resp = client.get(f"/api/perspectives/{pid}/snapshots/rollup")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert data["perspective_id"] == pid
        assert data["snapshot_count"] == 3
        # latest_snapshot_at must be ISO‑8601 string
        datetime.datetime.fromisoformat(data["latest_snapshot_at"])
        assert data["membership_sample"] == "member_0"

    print("PASS")
    raise SystemExit(0)