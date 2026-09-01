from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from typing import List
from app.db import get_session
from app.models import Org, ApiKey
from sqlalchemy.orm import Session
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

app = FastAPI()

class ApiKeyResponse(BaseModel):
    id: str
    label: str
    created_at: str

@app.get("/api/orgs/{org_id}/api_keys", response_model=List[ApiKeyResponse])
async def get_api_keys(org_id: str, db: Session = Depends(get_session)):
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    api_keys = db.query(ApiKey).filter(ApiKey.org_id == org_id).all()

    return [{
        "id": str(key.id),
        "label": key.label,
        "created_at": key.created_at.isoformat()
    } for key in api_keys]

if __name__ == "__main__":
    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    with SessionLocal() as db:
        test_org = Org(id="test-org-1", name="Test Org")
        db.add(test_org)
        db.commit()

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
        db.add(test_key1)
        db.add(test_key2)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/orgs/test-org-1/api_keys")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["id"] == "key-1"
    assert response.json()[0]["label"] == "Key 1"
    assert response.json()[1]["id"] == "key-2"
    assert response.json()[1]["label"] == "Key 2"

    print("PASS")