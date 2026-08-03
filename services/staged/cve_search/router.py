from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from .logic import search_cves
from app.db import get_session

router = APIRouter(prefix="/api")

class CVEResult(BaseModel):
    id: str
    summary: str
    severity: str
    package: str
    published_at: str

class CVESearchResponse(BaseModel):
    results: List[CVEResult]

@router.get("/cve/search", response_model=CVESearchResponse)
async def search_cve(q: str, session: Session = Depends(get_session)):
    results = search_cves(session, q)
    return {"results": results}

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import VulnAdvisory
    from datetime import datetime

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    test_session = SessionLocal()

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Seed test data
    test_session.add_all([
        VulnAdvisory(
            id="CVE-2023-1234",
            summary="Test vulnerability 1",
            severity="High",
            package="test-package-1",
            published_at=datetime.now()
        ),
        VulnAdvisory(
            id="CVE-2023-5678",
            summary="Test vulnerability 2",
            severity="Medium",
            package="test-package-2",
            published_at=datetime.now()
        )
    ])
    test_session.commit()

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    response = client.get("/api/cve/search?q=Test")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert response.json()["results"][0]["id"] == "CVE-2023-1234"
    assert response.json()["results"][1]["id"] == "CVE-2023-5678"

    print("PASS")