# services/staged/org_health/logic.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

# Real application data layer imports
from app.db import get_session, Base
from app.models import Org, User

router = APIRouter(prefix="/api")


class OrgHealthResponse(BaseModel):
    org_id: int
    user_count: int
    last_active_at: str | None = None
    api_key_count: int = 0


@router.get(
    "/orgs/{org_id}/health",
    response_model=OrgHealthResponse,
    status_code=status.HTTP_200_OK,
)
def get_org_health(org_id: int, session: Session = Depends(get_session)):
    # Verify org exists
    org = session.get(Org, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Org {org_id} not found",
        )

    # Count users belonging to the org
    user_count = (
        session.query(func.count(User.id))
        .filter(User.org_id == org_id)
        .scalar()
    )

    # Determine the most recent user creation timestamp as a proxy for activity
    last_active = (
        session.query(func.max(User.created_at))
        .filter(User.org_id == org_id)
        .scalar()
    )
    last_active_at = last_active.isoformat() if last_active else None

    # Placeholder for API key count – no dedicated table in the current schema
    api_key_count = 0

    return OrgHealthResponse(
        org_id=org_id,
        user_count=user_count,
        last_active_at=last_active_at,
        api_key_count=api_key_count,
    )


# --------------------------------------------------------------------------- #
# Self‑test ---------------------------------------------------------------
if __name__ == "__main__":
    # Build an in‑memory SQLite DB that mirrors the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override for the test FastAPI app
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSession() as sess:
        sess.add(Org(id=1, name="Test Org"))
        sess.add_all(
            [
                User(
                    id=1,
                    email="alice@example.com",
                    org_id=1,
                    password_hash="hash",
                    role="member",
                    created_at=func.now(),
                ),
                User(
                    id=2,
                    email="bob@example.com",
                    org_id=1,
                    password_hash="hash",
                    role="member",
                    created_at=func.now(),
                ),
            ]
        )
        sess.commit()

    # Run test request
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/orgs/1/health")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["org_id"] == 1
    assert data["user_count"] == 2
    print("PASS")