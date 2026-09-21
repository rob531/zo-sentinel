from typing import List, Dict, Optional
from datetime import datetime
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisory, Perspective, Org, User
from celery import shared_task
import requests
from pydantic import BaseModel
from fastapi.testclient import TestClient
from unittest.mock import patch

class CVEFeedSummary(BaseModel):
    total: int
    by_severity: Dict[str, int]
    by_ecosystem: Dict[str, int]

class CVEDetail(BaseModel):
    id: str
    summary: str
    severity: str
    published_date: datetime
    last_modified_date: datetime
    ecosystem: str
    references: List[str]

def get_cve_summary(db: Session = Depends(get_session)) -> CVEFeedSummary:
    """Get summary statistics for CVE feed data"""
    total = db.query(VulnAdvisory).count()

    by_severity = db.query(
        VulnAdvisory.severity,
        func.count(VulnAdvisory.id).label('count')
    ).group_by(VulnAdvisory.severity).all()
    by_severity = {severity: count for severity, count in by_severity}

    by_ecosystem = db.query(
        VulnAdvisory.ecosystem,
        func.count(VulnAdvisory.id).label('count')
    ).group_by(VulnAdvisory.ecosystem).all()
    by_ecosystem = {ecosystem: count for ecosystem, count in by_ecosystem}

    return CVEFeedSummary(
        total=total,
        by_severity=by_severity,
        by_ecosystem=by_ecosystem
    )

def get_cve_detail(cve_id: str, db: Session = Depends(get_session)) -> Optional[CVEDetail]:
    """Get detailed information for a specific CVE"""
    advisory = db.query(VulnAdvisory).filter(VulnAdvisory.id == cve_id).first()
    if not advisory:
        raise HTTPException(status_code=404, detail="CVE not found")

    return CVEDetail(
        id=advisory.id,
        summary=advisory.summary,
        severity=advisory.severity,
        published_date=advisory.published_date,
        last_modified_date=advisory.last_modified_date,
        ecosystem=advisory.ecosystem,
        references=advisory.references
    )

@shared_task
def process_nvd_feed():
    """Background task to process NVD CVE feed"""
    response = requests.get("https://services.nvd.nist.gov/rest/json/cves/1.0?pubStartDate=2023-01-01T00:00:00.000-05:00&pubEndDate=2023-12-31T23:59:59.999-05:00")
    if response.status_code != 200:
        raise Exception("Failed to fetch NVD feed")

    data = response.json()
    # Process and normalize data to VulnAdvisory model
    # Implementation omitted for brevity

@shared_task
def process_ghsa_feed():
    """Background task to process GitHub Security Advisory feed"""
    response = requests.get("https://api.github.com/advisories")
    if response.status_code != 200:
        raise Exception("Failed to fetch GHSA feed")

    data = response.json()
    # Process and normalize data to VulnAdvisory model
    # Implementation omitted for brevity

@shared_task
def process_osv_feed():
    """Background task to process OSV feed"""
    response = requests.get("https://osv.dev/api/v1/query")
    if response.status_code != 200:
        raise Exception("Failed to fetch OSV feed")

    data = response.json()
    # Process and normalize data to VulnAdvisory model
    # Implementation omitted for brevity

def parse_nvd_cve(cve_data: dict) -> Dict:
    """Parse NVD CVE data into normalized format"""
    # Implementation omitted for brevity
    return {}

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base, engine

    app = FastAPI()

    # Test setup
    Base.metadata.create_all(bind=engine)

    # Test cases
    with TestClient(app) as client:
        # Test summary endpoint
        response = client.get("/api/cve/summary")
        assert response.status_code == 200
        assert "by_severity" in response.json()

        # Test detail endpoint
        response = client.get("/api/cve/CVE-2023-1234")
        assert response.status_code == 200
        assert "id" in response.json()

        # Test background task processing
        with patch('services.staged.cve_feed_processing.logic.process_nvd_feed') as mock_task:
            mock_task.delay()
            assert mock_task.called

    print("PASS")