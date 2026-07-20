import json
import os
from typing import List, Dict, Optional
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, Org, User
import requests

app = FastAPI()

class UnmountedRouterReport(BaseModel):
    router_id: int
    org_name: str
    overall_risk: float
    auth_strength: float
    capability_breadth: float
    data_sensitivity: float
    network_egress: float
    maintainer_trust: float
    exploit_surface: float
    last_mounted: Optional[str]
    reason_unmounted: Optional[str]

class CensusReport(BaseModel):
    total_routers: int
    mounted_routers: int
    unmounted_routers: int
    unmounted_routers_report: List[UnmountedRouterReport]

def get_ratchet_census() -> Optional[Dict]:
    census_path = os.getenv("ZO_RATCHET_CENSUS", "artifacts/reachability_ratchet.json")
    try:
        with open(census_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def scan_unmounted_routers(session: Session) -> List[UnmountedRouterReport]:
    # Get all routers that are not currently mounted
    unmounted_routers = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.is_mounted == False
    ).all()

    reports = []
    for router in unmounted_routers:
        # Get the latest scores for this router
        scores = session.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.router_id == router.id
        ).order_by(MCPLLMAxisScores.timestamp.desc()).first()

        if not scores:
            continue

        # Get org name
        org = session.query(Org).get(router.org_id)
        org_name = org.name if org else "Unknown"

        report = UnmountedRouterReport(
            router_id=router.id,
            org_name=org_name,
            overall_risk=scores.overall_risk,
            auth_strength=scores.auth_strength,
            capability_breadth=scores.capability_breadth,
            data_sensitivity=scores.data_sensitivity,
            network_egress=scores.network_egress,
            maintainer_trust=scores.maintainer_trust,
            exploit_surface=scores.exploit_surface,
            last_mounted=router.last_mounted,
            reason_unmounted=router.reason_unmounted
        )
        reports.append(report)

    return reports

def get_unmounted_router_census(session: Session) -> CensusReport:
    # Get total number of routers
    total_routers = session.query(MCPServerRegistry).count()

    # Get number of mounted routers
    mounted_routers = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.is_mounted == True
    ).count()

    # Get unmounted routers report
    unmounted_routers_report = scan_unmounted_routers(session)

    return CensusReport(
        total_routers=total_routers,
        mounted_routers=mounted_routers,
        unmounted_routers=total_routers - mounted_routers,
        unmounted_routers_report=unmounted_routers_report
    )

@app.get("/unmounted-router-census", response_model=CensusReport)
async def unmounted_router_census(session: Session = Depends(get_session)):
    return get_unmounted_router_census(session)

def test_unmounted_router_census():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_org = Org(name="Test Org")
    test_session.add(test_org)
    test_session.commit()

    test_router = MCPServerRegistry(
        org_id=test_org.id,
        is_mounted=False,
        last_mounted="2023-01-01",
        reason_unmounted="Test reason"
    )
    test_session.add(test_router)
    test_session.commit()

    test_scores = MCPLLMAxisScores(
        router_id=test_router.id,
        overall_risk=0.5,
        auth_strength=0.6,
        capability_breadth=0.7,
        data_sensitivity=0.8,
        network_egress=0.9,
        maintainer_trust=0.4,
        exploit_surface=0.3
    )
    test_session.add(test_scores)
    test_session.commit()

    # Test the endpoint
    response = app.client.get("/unmounted-router-census")
    assert response.status_code == 200
    data = response.json()

    assert data["total_routers"] == 1
    assert data["mounted_routers"] == 0
    assert data["unmounted_routers"] == 1
    assert len(data["unmounted_routers_report"]) == 1
    assert data["unmounted_routers_report"][0]["router_id"] == test_router.id
    assert data["unmounted_routers_report"][0]["org_name"] == "Test Org"

    print("PASS")

if __name__ == "__main__":
    test_unmounted_router_census()