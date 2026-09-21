# services/staged/perspective_membership_snapshot/logic.py
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveSnapshot

router = APIRouter(prefix="/api")


def _latest_snapshot(perspective_id: int, db: Session) -> PerspectiveSnapshot:
    stmt = (
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
        .limit(1)
    )
    result = db.execute(stmt).scalar_one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="Perspective snapshot not found")
    return result


@router.get(
    "/perspectives/{perspective_id}/membership",
    response_model=Dict[str, Dict[str, Any]],
    tags=["perspective-membership-snapshot"],
)
def get_perspective_membership(
    perspective_id: int, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """
    Return the latest membership snapshot for a perspective.

    The response shape is:
    {
        "membership": {
            "<server_id>": {
                "tier": "<tier>",
                "last_seen": "<ISO8601 timestamp>"
            },
            ...
        }
    }
    """
    snapshot = _latest_snapshot(perspective_id, db)
    # `membership` column is expected to be a JSON‑compatible dict
    membership = snapshot.membership or {}
    return {"membership": membership}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build an in‑memory SQLite DB that mirrors the real models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base  # declarative base used by the real models

    ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(ENGINE)
    TestSessionLocal = sessionmaker(bind=ENGINE)

    # Seed test data
    test_db: Session = TestSessionLocal()
    test_snapshot = PerspectiveSnapshot(
        perspective_id=1,
        membership={
            "server-123": {"tier": "high", "last_seen": datetime.utcnow().isoformat()}
        },
        taken_at=datetime.utcnow(),
    )
    test_db.add(test_snapshot)
    test_db.commit()

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        return test_db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/perspectives/1/membership")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict) and "membership" in data, "Missing membership key"
    assert data["membership"], "Membership payload is empty"

    print("PASS")