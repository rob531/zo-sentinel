from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List
from app.db import get_session
from app.models import APIKey, Organization
from app.schemas import APIKeyCreate
from app.utils import get_current_organization_id

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

class APIKeyResponse(BaseModel):
    id: int
    key: str
    organization_id: int
    created_at: str

@router.get("/", response_model=List[APIKeyResponse])
def get_api_keys(
    db: Session = Depends(get_session),
    org_id: int = Depends(get_current_organization_id)
):
    keys = db.query(APIKey).filter(APIKey.organization_id == org_id).all()
    return [{"id": key.id, "key": key.key, "organization_id": key.organization_id, "created_at": str(key.created_at)} for key in keys]

@router.post("/", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    key_data: APIKeyCreate,
    db: Session = Depends(get_session),
    org_id: int = Depends(get_current_organization_id)
):
    # Check if organization exists
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Create new API key
    new_key = APIKey(
        key=key_data.key,
        organization_id=org_id
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return {
        "id": new_key.id,
        "key": new_key.key,
        "organization_id": new_key.organization_id,
        "created_at": str(new_key.created_at)
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test organization
    test_org = Organization(name="Test Org")
    with TestSession() as session:
        session.add(test_org)
        session.commit()

    # Create test client
    client = TestClient(app)

    # Test GET /api-keys
    response = client.get("/api-keys", headers={"X-Organization-ID": str(test_org.id)})
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Test POST /api-keys
    new_key_data = {"key": "test-key-123"}
    response = client.post(
        "/api-keys",
        json=new_key_data,
        headers={"X-Organization-ID": str(test_org.id)}
    )
    assert response.status_code == 201
    assert response.json()["key"] == new_key_data["key"]

    print("PASS")