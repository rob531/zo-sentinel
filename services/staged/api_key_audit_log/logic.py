from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime

# Real data layer imports
from app.db import get_session, Base
from app.models import ApiKey, Org

router = APIRouter()


@router.get(
    "/api/orgs/{org_id}/api-keys/audit",
    response_model=Dict[str, Any],
    tags=["api_key_audit_log"],
)
def get_api_key_audit_log(
    org_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    # Verify org exists
    org = session.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Fetch api keys for the org
    query = session.query(ApiKey).filter(ApiKey.org_id == org_id).order_by(ApiKey.created_at.desc())
    total = query.count()
    keys = query.offset(offset).limit(limit).all()

    # Build response
    result_keys: List[Dict[str, Any]] = []
    for key in keys:
        result_keys.append(
            {
                "id": key.id,
                "label": key.label,
                "created_at": key.created_at,
                "last_seen": None,  # column not present in schema
                "status": "active",  # placeholder status
                "rotation_count": 0,  # rotation info not stored in schema
            }
        )

    return {
        "org_id": org_id,
        "keys": result_keys,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB that mirrors the real models
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite://"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override for the test client
    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Populate test data
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        org1 = Org(id="org1", name="Org One", created_at=datetime.utcnow())
        org2 = Org(id="org2", name="Org Two", created_at=datetime.utcnow())
        db.add_all([org1, org2])
        db.flush()

        key1 = ApiKey(
            id="key1",
            label="First Key",
            key_hash="hash1",
            org_id="org1",
            created_at=datetime.utcnow(),
        )
        key2 = ApiKey(
            id="key2",
            label="Second Key",
            key_hash="hash2",
            org_id="org1",
            created_at=datetime.utcnow(),
        )
        key3 = ApiKey(
            id="key3",
            label="Third Key",
            key_hash="hash3",
            org_id="org2",
            created_at=datetime.utcnow(),
        )
        db.add_all([key1, key2, key3])
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with overridden dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    response = client.get("/api/orgs/org1/api-keys/audit")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    data = response.json()
    assert data["org_id"] == "org1"
    assert isinstance(data["keys"], list)
    assert len(data["keys"]) >= 1, "Expected at least one API key in response"

    print("PASS")