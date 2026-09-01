from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_advisory_freshness_stats
from pydantic import BaseModel

router = APIRouter(prefix="/api/advisory")

class FeedStats(BaseModel):
    name: str
    avg_age_hours: float
    max_age_hours: float
    count: int

class AdvisoryFreshnessResponse(BaseModel):
    feeds: list[FeedStats]

@router.get("/freshness", response_model=AdvisoryFreshnessResponse)
def get_freshness_stats(session: Session = Depends(get_session)):
    stats = get_advisory_freshness_stats(session)
    return {"feeds": stats}

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import VulnAdvisory
    from datetime import datetime, timedelta
    from fastapi.testclient import TestClient
    from app.main import app

    # Test setup
    Base.metadata.create_all(engine)

    # Override session for testing
    app.dependency_overrides[get_session] = lambda: Session(engine)

    # Seed test data
    with Session(engine) as session:
        session.execute(
            "INSERT INTO vuln_advisories (id, title, published_at, feed) VALUES "
            "('CVE-2023-1234', 'Test Advisory 1', %s, 'Feed A'), "
            "('CVE-2023-5678', 'Test Advisory 2', %s, 'Feed A'), "
            "('CVE-2023-9012', 'Test Advisory 3', %s, 'Feed B')",
            [
                (datetime.now() - timedelta(hours=12)).isoformat(),
                (datetime.now() - timedelta(hours=24)).isoformat(),
                (datetime.now() - timedelta(hours=6)).isoformat(),
            ],
        )
        session.commit()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/api/advisory/freshness")
    assert response.status_code == 200
    data = response.json()

    # Assertions
    assert len(data["feeds"]) == 2
    feed_a = next(f for f in data["feeds"] if f["name"] == "Feed A")
    assert feed_a["avg_age_hours"] > 10

    print("PASS")