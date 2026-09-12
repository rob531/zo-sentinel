from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter()

class NormalizedAdvisory(BaseModel):
    id: str
    ecosystem: str
    package: str
    affected_ranges: List[str]
    aliases: List[str]
    severity: str
    summary: str
    published_at: str

@router.get("/normalize", response_model=List[NormalizedAdvisory])
async def normalize_advisory_feed(feed: str, session: Session = Depends(get_session)):
    advisories = session.query(VulnAdvisory).filter(VulnAdvisory.feed == feed).all()
    if not advisories:
        raise HTTPException(status_code=404, detail="No advisories found for the specified feed")

    normalized_advisories = []
    for advisory in advisories:
        normalized_advisory = NormalizedAdvisory(
            id=advisory.id,
            ecosystem=advisory.ecosystem,
            package=advisory.package,
            affected_ranges=advisory.affected_ranges,
            aliases=advisory.aliases,
            severity=advisory.severity,
            summary=advisory.summary,
            published_at=advisory.published_at
        )
        normalized_advisories.append(normalized_advisory)

    return normalized_advisories

if __name__ == "__main__":
    import pytest
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router, prefix="/api/vuln")

    client = TestClient(app)

    def test_normalize_advisory_feed():
        response = client.get("/api/vuln/normalize?feed=osv")
        assert response.status_code == 200
        assert len(response.json()) == 3
        for advisory in response.json():
            assert len(advisory) == 9

    pytest.main(["-v", __file__])
    print("PASS")