from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

def calculate_coverage(servers: List[McpServerRegistry]) -> Dict[str, Dict[str, float]]:
    coverage_report = {}
    for server in servers:
        family = server.meta.get('family', 'unknown')
        if family not in coverage_report:
            coverage_report[family] = {'covered_count': 0, 'total_count': 0}
        coverage_report[family]['total_count'] += 1
        if server.verdict == 'covered':
            coverage_report[family]['covered_count'] += 1

    for family in coverage_report:
        total = coverage_report[family]['total_count']
        covered = coverage_report[family]['covered_count']
        coverage_report[family]['coverage_percentage'] = (covered / total * 100) if total > 0 else 0.0

    return coverage_report

@router.get("/api/reports/family-coverage")
async def get_family_coverage_report(db: Session = Depends(get_session)) -> Dict[str, Dict[str, float]]:
    servers = db.query(McpServerRegistry).all()
    return calculate_coverage(servers)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Test data
    test_servers = [
        McpServerRegistry(
            server_id="1",
            name="Server 1",
            verdict="covered",
            meta={"family": "family1"},
            confidence=0.9,
            description="Test server 1",
            first_seen="2023-01-01",
            last_assessed="2023-01-01",
            last_scanned="2023-01-01",
            last_seen="2023-01-01",
            registry_source="test",
            risk_tier=1,
            scan_count=1,
            trust_score=0.9,
            url="http://test1.com",
            verdict_reasoning="Test reasoning 1"
        ),
        McpServerRegistry(
            server_id="2",
            name="Server 2",
            verdict="uncovered",
            meta={"family": "family1"},
            confidence=0.8,
            description="Test server 2",
            first_seen="2023-01-01",
            last_assessed="2023-01-01",
            last_scanned="2023-01-01",
            last_seen="2023-01-01",
            registry_source="test",
            risk_tier=2,
            scan_count=1,
            trust_score=0.8,
            url="http://test2.com",
            verdict_reasoning="Test reasoning 2"
        ),
        McpServerRegistry(
            server_id="3",
            name="Server 3",
            verdict="covered",
            meta={"family": "family2"},
            confidence=0.7,
            description="Test server 3",
            first_seen="2023-01-01",
            last_assessed="2023-01-01",
            last_scanned="2023-01-01",
            last_seen="2023-01-01",
            registry_source="test",
            risk_tier=3,
            scan_count=1,
            trust_score=0.7,
            url="http://test3.com",
            verdict_reasoning="Test reasoning 3"
        )
    ]

    with TestSessionLocal() as db:
        for server in test_servers:
            db.add(server)
        db.commit()

    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    response = client.get("/api/reports/family-coverage")
    assert response.status_code == 200
    report = response.json()

    expected_report = {
        "family1": {
            "covered_count": 1,
            "total_count": 2,
            "coverage_percentage": 50.0
        },
        "family2": {
            "covered_count": 1,
            "total_count": 1,
            "coverage_percentage": 100.0
        }
    }

    assert report == expected_report
    print("PASS")