from typing import List, Dict, Any
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisories
from pydantic import BaseModel
import requests
import json

class ImportedAdvisory(BaseModel):
    id: str
    summary: str

class ImportResponse(BaseModel):
    imported_advisories: List[ImportedAdvisory]

def parse_ghsa_feed(feed_url: str) -> List[Dict[str, Any]]:
    response = requests.get(feed_url)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch GHSA feed")
    return response.json()

def process_advisories(advisories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    processed = []
    for advisory in advisories:
        processed.append({
            "id": advisory.get("id"),
            "summary": advisory.get("summary"),
            "published": advisory.get("published"),
            "modified": advisory.get("modified"),
            "references": json.dumps(advisory.get("references", [])),
            "affected": json.dumps(advisory.get("affected", [])),
            "aliases": json.dumps(advisory.get("aliases", [])),
            "database_specific": json.dumps(advisory.get("database_specific", {})),
            "details": advisory.get("details"),
            "severity": advisory.get("severity"),
            "source": "GHSA"
        })
    return processed

def import_ghsa_feed(db: Session = Depends(get_session), feed_url: str = "https://github-advisory-database.github.io/feeds/ghsa.json") -> ImportResponse:
    advisories = parse_ghsa_feed(feed_url)
    processed_advisories = process_advisories(advisories)

    for advisory in processed_advisories:
        db_advisory = VulnAdvisories(**advisory)
        db.add(db_advisory)

    db.commit()
    db.refresh(db_advisory)

    return ImportResponse(
        imported_advisories=[
            ImportedAdvisory(id=adv["id"], summary=adv["summary"])
            for adv in processed_advisories
        ]
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test data
    test_feed = {
        "advisories": [
            {
                "id": "GHSA-1234-5678-90ab-cdef",
                "summary": "Test advisory 1",
                "published": "2023-01-01T00:00:00Z",
                "modified": "2023-01-02T00:00:00Z",
                "references": [{"url": "https://example.com/1"}],
                "affected": [{"package": {"name": "test-package"}}],
                "aliases": ["CVE-2023-1234"],
                "database_specific": {"severity": "high"},
                "details": "Test details",
                "severity": "high"
            },
            {
                "id": "GHSA-5678-90ab-cdef-1234",
                "summary": "Test advisory 2",
                "published": "2023-01-03T00:00:00Z",
                "modified": "2023-01-04T00:00:00Z",
                "references": [{"url": "https://example.com/2"}],
                "affected": [{"package": {"name": "test-package-2"}}],
                "aliases": ["CVE-2023-5678"],
                "database_specific": {"severity": "critical"},
                "details": "Test details 2",
                "severity": "critical"
            }
        ]
    }

    # Mock the requests.get to return our test data
    def mock_get(url):
        return type('Response', (), {
            'status_code': 200,
            'json': lambda: test_feed
        })()

    requests.get = mock_get

    # Run the import
    response = import_ghsa_feed(feed_url="https://example.com/ghsa.json")

    # Verify the results
    assert response.imported_advisories[0].id == "GHSA-1234-5678-90ab-cdef"
    assert len(response.imported_advisories) == 2

    print("PASS")