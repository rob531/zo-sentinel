import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real application data layer imports
from app.db import get_session  # Dependency that provides a SQLAlchemy Session
from app.models import Base, VulnAdvisory  # The declarative Base and the advisory model


# ----------------------------------------------------------------------
# Pydantic schema for the advisory response
# ----------------------------------------------------------------------
class AdvisoryOut(BaseModel):
    id: str
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Optional[str] = None
    aliases: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime.datetime] = None

    class Config:
        orm_mode = True


# ----------------------------------------------------------------------
# FastAPI router / application
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get("/vuln/advisories", response_model=List[AdvisoryOut])
def list_advisories(db: Session = Depends(get_session)):
    """Return all vulnerability advisories."""
    advisories = db.query(VulnAdvisory).all()
    return advisories


app = FastAPI()
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test (runnable with `python -m services.staged.vuln_advisory_management.contract`)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Create an isolated SQLite database for the test
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Override the FastAPI dependency to use the test session
    def _test_get_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _test_get_session

    # Create tables using the real declarative Base
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------
    # Seed the test database with three advisories
    # ------------------------------------------------------------------
    seed_data = [
        VulnAdvisory(
            id="ADV-001",
            feed="npm",
            summary="Test advisory 1",
            severity="high",
            ecosystem="npm",
            package="package-one",
            affected_ranges=">=1.0.0 <2.0.0",
            aliases="CVE-2021-0001",
            source_url="https://example.com/adv1",
            published_at=datetime.datetime(2021, 1, 1, 0, 0, 0),
        ),
        VulnAdvisory(
            id="ADV-002",
            feed="pypi",
            summary="Test advisory 2",
            severity="medium",
            ecosystem="pypi",
            package="package-two",
            affected_ranges=">=0.5.0 <1.0.0",
            aliases="CVE-2021-0002",
            source_url="https://example.com/adv2",
            published_at=datetime.datetime(2021, 2, 1, 0, 0, 0),
        ),
        VulnAdvisory(
            id="ADV-003",
            feed="rubygems",
            summary="Test advisory 3",
            severity="low",
            ecosystem="rubygems",
            package="package-three",
            affected_ranges=">=2.0.0 <3.0.0",
            aliases="CVE-2021-0003",
            source_url="https://example.com/adv3",
            published_at=datetime.datetime(2021, 3, 1, 0, 0, 0),
        ),
    ]

    with TestSessionLocal() as db:
        db.add_all(seed_data)
        db.commit()

    # ------------------------------------------------------------------
    # Run the acceptance test
    # ------------------------------------------------------------------
    client = TestClient(app)
    resp = client.get("/api/vuln/advisories")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list), "Response is not a list"
    assert len(data) >= 3, "Less than three advisories returned"
    ids = {adv["id"] for adv in data}
    assert "ADV-001" in ids, "Known advisory ID missing"
    print("PASS")