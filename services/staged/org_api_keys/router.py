from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List
from pydantic import BaseModel

from app.db import get_session
from app.models import Org, ApiKey

router = APIRouter(prefix="/api")

class ApiKeyResponse(BaseModel):
    id: str
    label: str
    created_at: str

@router.get("/orgs/{org_id}/api_keys", response_model=List[ApiKeyResponse])
def get_api_keys(org_id: str, session: Session = Depends(get_session)):
    org = session.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    api_keys = session.query(ApiKey).filter(ApiKey.org_id == org_id).all()

    return [{
        "id": str(key.id),
        "label": key.label,
        "created_at": key.created_at.isoformat()
    } for key in api_keys]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Create test data
    test_org = Org(id="test-org-1", name="Test Org")
    test_session.add(test_org)
    test_session.commit()

    test_key1 = ApiKey(
        id="key-1",
        org_id="test-org-1",
        key_hash="hash1",
        label="Key 1",
        created_at=datetime.now()
    )
    test_key2 = ApiKey(
        id="key-2",
        org_id="test-org-1",
        key_hash="hash2",
        label="Key 2",
        created_at=datetime.now()
    )
    test_session.add_all([test_key1, test_key2])
    test_session.commit()

    # Create test client
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    client = TestClient(test_app)

    # Test endpoint
    response = client.get("/api/orgs/test-org-1/api_keys")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["id"] == "key-1"
    assert response.json()[0]["label"] == "Key 1"
    assert response.json()[1]["id"] == "key-2"
    assert response.json()[1]["label"] == "Key 2"

    print("PASS")