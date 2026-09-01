from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

class CVEResult(BaseModel):
    id: str
    summary: str
    severity: str
    package: str
    published_at: str

class CVESearchResponse(BaseModel):
    results: List[CVEResult]

app = FastAPI()

@app.get("/api/cve/search", response_model=CVESearchResponse)
async def search_cves(
    q: str,
    db: Session = Depends(get_session)
) -> CVESearchResponse:
    query = f"%{q}%"
    advisories = db.query(VulnAdvisory).filter(
        (VulnAdvisory.summary.like(query)) |
        (VulnAdvisory.severity.like(query)) |
        (VulnAdvisory.package.like(query))
    ).all()

    results = [
        CVEResult(
            id=str(adv.id),
            summary=adv.summary,
            severity=adv.severity,
            package=adv.package,
            published_at=str(adv.published_at)
        ) for adv in advisories
    ]

    return CVESearchResponse(results=results)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        session.add_all([
            VulnAdvisory(
                id=1,
                summary="Test CVE 1 summary",
                severity="high",
                package="test-package-1",
                published_at="2023-01-01"
            ),
            VulnAdvisory(
                id=2,
                summary="Test CVE 2 summary",
                severity="medium",
                package="test-package-2",
                published_at="2023-01-02"
            )
        ])
        session.commit()

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/cve/search?q=Test")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert response.json()["results"][0]["summary"] == "Test CVE 1 summary"
    assert response.json()["results"][1]["summary"] == "Test CVE 2 summary"

    print("PASS")