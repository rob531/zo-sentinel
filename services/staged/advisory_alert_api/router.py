"""Router for advisory_alert_api service."""

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Real data layer imports (must not be replaced)
from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/api", tags=["advisory_alert_api"])

class AdvisoryAlert(BaseModel):
    """Response model for vulnerability advisory alerts."""
    feed: str
    id: str
    summary: str
    severity: str
    published_at: datetime
    affected_ranges: List[str]

    class Config:
        orm_mode = True

@router.get(
    "/advisories/alerts",
    response_model=List[AdvisoryAlert],
    summary="Get recent high-severity vulnerability advisories",
    description="Returns active advisories from NVD, OSV, and GHSA feeds with CRITICAL or HIGH severity published in the last 7 days"
)
def get_advisory_alerts(session: Session = Depends(get_session)):
    """Endpoint to fetch recent high-severity vulnerability alerts."""
    try:
        cutoff = datetime.utcnow() - timedelta(days=7)

        advisories = (
            session.query(VulnAdvisory)
            .filter(
                VulnAdvisory.active.is_(True),
                VulnAdvisory.feed.in_(["NVD", "OSV", "GHSA"]),
                VulnAdvisory.severity.in_(["CRITICAL", "HIGH"]),
                VulnAdvisory.published_at >= cutoff
            )
            .all()
        )

        # Process affected_ranges which might be stored as JSON or string
        alerts = []
        for adv in advisories:
            ranges = adv.affected_ranges
            if isinstance(ranges, str):
                ranges = [ranges]
            alerts.append(
                AdvisoryAlert(
                    feed=adv.feed,
                    id=adv.id,
                    summary=adv.summary,
                    severity=adv.severity,
                    published_at=adv.published_at,
                    affected_ranges=ranges
                )
            )

        return alerts

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching advisory alerts: {str(e)}"
        )

# --------------------------------------------------------------------------- #
# Self-test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In-memory SQLite for testing
    TEST_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.db import Base  # type: ignore
    Base.metadata.create_all(bind=engine)

    # Seed test data
    test_session = SessionLocal()
    now = datetime.utcnow()
    sample_data = [
        VulnAdvisory(
            id="NVD-2026-001",
            feed="NVD",
            summary="Critical buffer overflow vulnerability",
            severity="CRITICAL",
            published_at=now - timedelta(days=1),
            affected_ranges=["1.0.0 - 1.2.3"],
            active=True
        ),
        VulnAdvisory(
            id="OSV-2026-002",
            feed="OSV",
            summary="High severity SQL injection",
            severity="HIGH",
            published_at=now - timedelta(days=2),
            affected_ranges=["2.0.0 - 2.1.5"],
            active=True
        ),
        VulnAdvisory(
            id="GHSA-2026-003",
            feed="GHSA",
            summary="High severity XSS vulnerability",
            severity="HIGH",
            published_at=now - timedelta(days=3),
            affected_ranges=["3.0.0 - 3.2.1"],
            active=True
        ),
        VulnAdvisory(
            id="NVD-2026-004",
            feed="NVD",
            summary="Medium severity CSRF",
            severity="MEDIUM",
            published_at=now - timedelta(days=4),
            affected_ranges=["4.0.0 - 4.1.0"],
            active=True
        )
    ]
    test_session.add_all(sample_data)
    test_session.commit()

    # Dependency override
    def get_test_session() -> Session:
        try:
            yield test_session
        finally:
            test_session.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/advisories/alerts")
    assert response.status_code == 200, f"Unexpected status code: {response.status_code}"

    alerts = response.json()
    assert len(alerts) == 3, f"Expected 3 alerts, got {len(alerts)}"

    # Verify we got one from each feed
    feeds = {alert["feed"] for alert in alerts}
    assert feeds == {"NVD", "OSV", "GHSA"}, f"Missing feeds: {feeds}"

    # Verify we got at least one CRITICAL
    severities = {alert["severity"] for alert in alerts}
    assert "CRITICAL" in severities, f"Missing CRITICAL severity: {severities}"

    print("PASS")
