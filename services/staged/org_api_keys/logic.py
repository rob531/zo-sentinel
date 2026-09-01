from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from pydantic import BaseModel

from app.db import get_session
from app.models import Org, ApiKey

class ApiKeyResponse(BaseModel):
    id: str
    label: str
    created_at: str

def get_org_api_keys(org_id: str, db: Session = Depends(get_session)) -> List[ApiKeyResponse]:
    # Verify the org exists
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    # Get all api_keys for the org
    api_keys = db.query(ApiKey).filter(ApiKey.org_id == org_id).all()

    # Convert to response format
    return [
        ApiKeyResponse(
            id=str(key.id),
            label=key.label,
            created_at=key.created_at.isoformat()
        )
        for key in api_keys
    ]

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Include the router (simplified for testing)
    from fastapi import APIRouter
    router = APIRouter()
    router.get("/api/orgs/{org_id}/api_keys")(get_org_api_keys)
    app.include_router(router)

    # Insert test data
    test_session = TestSession()
    test_org = Org(id="test-org-1", name="Test Org")
    test_session.add(test_org)
    test_session.commit()

    test_api_key1 = ApiKey(
        id="key-1",
        org_id="test-org-1",
        key_hash="hash1",
        label="Key 1",
        created_at=datetime.now()
    )
    test_api_key2 = ApiKey(
        id="key-2",
        org_id="test-org-1",
        key_hash="hash2",
        label="Key 2",
        created_at=datetime.now()
    )
    test_session.add_all([test_api_key1, test_api_key2])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/orgs/test-org-1/api_keys")

    # Verify the response
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["id"] == "key-1"
    assert response.json()[0]["label"] == "Key 1"
    assert response.json()[1]["id"] == "key-2"
    assert response.json()[1]["label"] == "Key 2"

    print("PASS")