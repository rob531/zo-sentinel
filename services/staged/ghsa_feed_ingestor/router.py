from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List

from app.db import get_session
from app.models import VulnAdvisories

from .logic import import_ghsa_feed

router = APIRouter(prefix="/api")

class AdvisorySummary(BaseModel):
    id: str
    summary: str

class ImportResponse(BaseModel):
    imported_advisories: List[AdvisorySummary]

@router.post("/feed/ghsa", response_model=ImportResponse)
async def import_ghsa_feed_endpoint(session=Depends(get_session)):
    try:
        imported = import_ghsa_feed(session)
        return {
            "imported_advisories": [
                {"id": adv.id, "summary": adv.summary}
                for adv in imported
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base

    # Override the session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # Create test client
    client = TestClient(app)

    # Test data
    test_feed_data = {
        "advisories": [
            {
                "id": "GHSA-1234-5678-90ab-cdef",
                "summary": "Test vulnerability 1"
            },
            {
                "id": "GHSA-2345-6789-01bc-defg",
                "summary": "Test vulnerability 2"
            }
        ]
    }

    # Test import
    response = client.post("/api/feed/ghsa", json=test_feed_data)
    assert response.status_code == 200
    assert len(response.json()["imported_advisories"]) == 2
    assert any(adv["id"] == "GHSA-1234-5678-90ab-cdef" for adv in response.json()["imported_advisories"])

    print("PASS")