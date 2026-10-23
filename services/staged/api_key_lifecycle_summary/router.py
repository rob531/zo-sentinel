"""API router for the `api_key_lifecycle_summary` service.

Exposes:
    GET /api/admin/keys/lifecycle-summary
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# Real application dependencies – do NOT replace with test doubles here.
from app.db import get_session
from .logic import get_api_key_lifecycle_summary

router = APIRouter(prefix="/api/admin", tags=["api_key_lifecycle_summary"])


@router.get("/keys/lifecycle-summary")
def lifecycle_summary(session: Session = Depends(get_session)):
    """Return a JSON summary of API‑key lifecycle statistics."""
    return get_api_key_lifecycle_summary(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime as _dt
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base  # declarative base used by the real models
    from app.models import ApiKey, Org, User

    # ------------------------------------------------------------------- #
    # Test database setup (in‑memory SQLite)
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite:///:memory:"

    test_engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    Base.metadata.create_all(bind=test_engine)

    # ------------------------------------------------------------------- #
    # Dependency override for the FastAPI app used in the test
    # ------------------------------------------------------------------- #
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    now = _dt.datetime.utcnow()

    with TestSessionLocal() as db:
        # three organisations
        orgs = [
            Org(id=1, name="Org‑One", created_at=now - _dt.timedelta(days=30)),
            Org(id=2, name="Org‑Two", created_at=now - _dt.timedelta(days=20)),
            Org(id=3, name="Org‑Three", created_at=now - _dt.timedelta(days=10)),
        ]
        db.add_all(orgs)
        db.flush()  # assign PKs if needed

        # five API keys spread across the orgs
        keys = [
            ApiKey(
                id=1,
                label="Key‑A1",
                org_id=1,
                created_at=now - _dt.timedelta(days=25),
                key_hash="hash-a1",
            ),
            ApiKey(
                id=2,
                label="Key‑A2",
                org_id=1,
                created_at=now - _dt.timedelta(days=5),
                key_hash="hash-a2",
            ),
            ApiKey(
                id=3,
                label="Key‑B1",
                org_id=2,
                created_at=now - _dt.timedelta(days=15),
                key_hash="hash-b1",
            ),
            ApiKey(
                id=4,
                label="Key‑C1",
                org_id=3,
                created_at=now - _dt.timedelta(days=9),
                key_hash="hash-c1",
            ),
            ApiKey(
                id=5,
                label="Key‑C2",
                org_id=3,
                created_at=now - _dt.timedelta(days=2),
                key_hash="hash-c2",
            ),
        ]
        db.add_all(keys)
        db.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app for the test
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request & validate response shape
    # ------------------------------------------------------------------- #
    response = client.get("/api/admin/keys/lifecycle-summary")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()

    # top‑level keys
    for top_key in ("total", "active", "revoked", "expired", "per_org", "recent_rotations"):
        assert top_key in data, f"Missing top‑level key: {top_key}"

    # counts
    assert data["total"] == 5
    assert data["active"] == 5
    assert data["revoked"] == 0
    assert data["expired"] == 0

    # per‑org aggregation
    per_org = {item["org_id"]: item for item in data["per_org"]}
    assert len(per_org) == 3
    assert per_org[1]["key_count"] == 2
    assert per_org[2]["key_count"] == 1
    assert per_org[3]["key_count"] == 2

    # recent rotations – our implementation returns an empty list
    assert isinstance(data["recent_rotations"], list)

    print("PASS")