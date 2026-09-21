from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.db import get_session
from .logic import fetch_and_parse_osv_advisories

router = APIRouter(prefix="/api")

class OSVFeedResponse(BaseModel):
    count: int
    last_published_at: Optional[datetime]

@router.post("/feeds/osv", response_model=OSVFeedResponse)
async def ingest_osv_feed(session=Depends(get_session)):
    try:
        count, last_published_at = await fetch_and_parse_osv_advisories(session)
        return {"count": count, "last_published_at": last_published_at}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import VulnAdvisories
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create tables
    VulnAdvisories.__table__.create(test_engine)

    # Mock the OSV feed response
    mock_osv_feed = {
        "advisories": [
            {
                "id": "GHSA-1234-5678-90ab-cdef",
                "published": "2023-01-01T00:00:00Z",
                "aliases": ["CVE-2023-1234"],
                "content": "Advisory content 1"
            },
            {
                "id": "GHSA-2345-6789-01ab-cdef",
                "published": "2023-01-02T00:00:00Z",
                "aliases": ["CVE-2023-2345"],
                "content": "Advisory content 2"
            },
            {
                "id": "GHSA-3456-7890-12ab-cdef",
                "published": "2023-01-03T00:00:00Z",
                "aliases": ["CVE-2023-3456"],
                "content": "Advisory content 3"
            },
            {
                "id": "GHSA-4567-8901-23ab-cdef",
                "published": "2023-01-04T00:00:00Z",
                "aliases": ["CVE-2023-4567"],
                "content": "Advisory content 4"
            }
        ]
    }

    # Mock the fetch_and_parse_osv_advisories function
    async def mock_fetch_and_parse_osv_advisories(session: Session):
        for advisory in mock_osv_feed["advisories"]:
            adv = VulnAdvisories(
                id=advisory["id"],
                published=datetime.fromisoformat(advisory["published"].replace("Z", "+00:00")),
                aliases=advisory["aliases"],
                content=advisory["content"],
                content_hash="mock_hash"
            )
            session.add(adv)
        session.commit()
        return len(mock_osv_feed["advisories"]), datetime.fromisoformat(mock_osv_feed["advisories"][-1]["published"].replace("Z", "+00:00"))

    app.dependency_overrides[fetch_and_parse_osv_advisories] = mock_fetch_and_parse_osv_advisories

    # Create the FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)

    # Test the endpoint
    client = TestClient(app)
    response = client.post("/api/feeds/osv")
    assert response.status_code == 200
    assert response.json()["count"] == 4
    print("PASS")