from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from app.db import get_session
from app.models import VulnLink, VulnAdvisory

app = FastAPI()

class CVESearchResult(BaseModel):
    id: int
    feed: str
    severity: str
    ecosystem: str
    package: str
    summary: str
    affected_ranges: str
    source_url: str
    published_at: str

class ResponseModel(BaseModel):
    results: List[CVESearchResult]

def get_db() -> Session:
    return Depends(get_session)

@app.get("/api/cve/search", response_model=ResponseModel)
async def search_cve(server_id: str, db: Session = Depends(get_db)) -> ResponseModel:
    results = db.query(VulnLink, VulnAdvisory).join(
        VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id
    ).filter(VulnLink.server_id == server_id).all()

    cve_results = []
    for link, advisory in results:
        cve_results.append({
            "id": advisory.id,
            "feed": advisory.feed,
            "severity": advisory.severity,
            "ecosystem": advisory.ecosystem,
            "package": advisory.package,
            "summary": advisory.summary,
            "affected_ranges": advisory.affected_ranges,
            "source_url": advisory.source_url,
            "published_at": advisory.published_at
        })

    return ResponseModel(results=cve_results)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_db] = lambda: SessionLocal()

    # Seed test data
    db = SessionLocal()
    server_1 = "server_1"
    server_2 = "server_2"

    advisory_1 = VulnAdvisory(
        id=1,
        feed="nvd",
        severity="high",
        ecosystem="python",
        package="requests",
        summary="Security vulnerability in requests",
        affected_ranges="<2.26.0",
        source_url="https://example.com/cve1",
        published_at="2021-01-01"
    )
    advisory_2 = VulnAdvisory(
        id=2,
        feed="nvd",
        severity="medium",
        ecosystem="python",
        package="flask",
        summary="Security vulnerability in flask",
        affected_ranges="<2.0.0",
        source_url="https://example.com/cve2",
        published_at="2021-02-01"
    )

    db.add_all([advisory_1, advisory_2])
    db.commit()

    link_1 = VulnLink(server_id=server_1, advisory_id=1)
    link_2 = VulnLink(server_id=server_1, advisory_id=2)
    link_3 = VulnLink(server_id=server_2, advisory_id=1)

    db.add_all([link_1, link_2, link_3])
    db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/cve/search?server_id={server_1}")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert response.json()["results"][0]["severity"] == "high"

    print("PASS")