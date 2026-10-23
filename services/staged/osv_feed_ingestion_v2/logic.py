import json
import requests
from datetime import datetime
from typing import List, Dict, Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisory

OSV_FEED_URL = "https://storage.googleapis.com/osv-vulnerabilities/feed.json"

def fetch_osv_feed() -> List[Dict]:
    response = requests.get(OSV_FEED_URL)
    response.raise_for_status()
    return response.json()

def parse_advisory(advisory: Dict) -> Dict:
    return {
        "id": advisory["id"],
        "aliases": advisory.get("aliases", []),
        "published": advisory["published"],
        "modified": advisory.get("modified"),
        "summary": advisory.get("summary", ""),
        "details": advisory.get("details", ""),
        "affected": advisory.get("affected", []),
        "references": advisory.get("references", []),
        "content_hash": hash(json.dumps(advisory, sort_keys=True))
    }

def store_advisories(session: Session, advisories: List[Dict]) -> None:
    for advisory in advisories:
        parsed = parse_advisory(advisory)
        db_advisory = VulnAdvisory(**parsed)
        session.merge(db_advisory)
    session.commit()

def process_osv_feed(session: Session = Depends(get_session)) -> Dict:
    advisories = fetch_osv_feed()
    store_advisories(session, advisories)
    last_published = max(advisory["published"] for advisory in advisories)
    return {
        "count": len(advisories),
        "last_published_at": last_published
    }

if __name__ == "__main__":
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Mock OSV feed
    mock_feed = [
        {
            "id": "CVE-2023-1234",
            "aliases": ["GHSA-1234-5678"],
            "published": "2023-01-01T00:00:00Z",
            "details": "Test advisory 1"
        },
        {
            "id": "CVE-2023-5678",
            "aliases": ["GHSA-5678-1234"],
            "published": "2023-01-02T00:00:00Z",
            "details": "Test advisory 2"
        },
        {
            "id": "CVE-2023-9012",
            "aliases": ["GHSA-9012-3456"],
            "published": "2023-01-03T00:00:00Z",
            "details": "Test advisory 3"
        },
        {
            "id": "CVE-2023-3456",
            "aliases": ["GHSA-3456-9012"],
            "published": "2023-01-04T00:00:00Z",
            "details": "Test advisory 4"
        }
    ]

    def mock_fetch_osv_feed():
        return mock_feed

    # Override for self-test
    original_fetch = fetch_osv_feed
    fetch_osv_feed = mock_fetch_osv_feed

    try:
        result = process_osv_feed()
        assert result["count"] == 4
        print("PASS")
    finally:
        fetch_osv_feed = original_fetch
        app.dependency_overrides.clear()