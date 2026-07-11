from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from app.db import get_session
from app.models import APIKey
from sqlalchemy.orm import Session

router = APIRouter()

class APIKeyResponse(BaseModel):
    id: int
    org_id: int
    key_hash: str
    label: str
    created_at: str

@router.get("/admin/api_keys", response_model=List[APIKeyResponse])
async def get_api_keys(db: Session = Depends(get_session)):
    try:
        keys = db.query(APIKey).all()
        return [
            APIKeyResponse(
                id=key.id,
                org_id=key.org_id,
                key_hash=key.key_hash,
                label=key.label,
                created_at=str(key.created_at)
            )
            for key in keys
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Setup in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create a test app and client
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    client = TestClient(test_app)

    # Seed test data
    test_session = TestSession()
    test_session.add(APIKey(
        org_id=1,
        key_hash="test_hash_1",
        label="Test Key 1",
        created_at="2023-01-01T00:00:00"
    ))
    test_session.add(APIKey(
        org_id=2,
        key_hash="test_hash_2",
        label="Test Key 2",
        created_at="2023-01-02T00:00:00"
    ))
    test_session.commit()

    # Test the endpoint
    response = client.get("/admin/api_keys")
    assert response.status_code == 200
    assert len(response.json()) == 2
    print("PASS")