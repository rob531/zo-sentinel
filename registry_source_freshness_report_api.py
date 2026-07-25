from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime, timedelta
from statistics import mean
from typing import List, Optional
import requests
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class SourceFreshness(BaseModel):
    source: str
    total_servers: int
    avg_days_since_last_scan: float
    max_days_since_last_scan: int
    stale_servers_pct: float
    oldest_server_seen_at: str

class FreshnessReport(BaseModel):
    sources: List[SourceFreshness]
    overall_stale_pct: float
    alerts: List[str]

def get_stale_threshold_days(stale_threshold_days: Optional[int] = Query(30)) -> int:
    return stale_threshold_days

def calculate_days_since_last_scan(last_scanned: datetime) -> int:
    return (datetime.utcnow() - last_scanned).days

def get_registry_data(session=Depends(get_session)) -> List[MCPServerRegistry]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT * FROM mcp_server_registry",
                "params": []
            }
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/registry/freshness-report", response_model=FreshnessReport)
async def get_freshness_report(
    stale_threshold_days: int = Depends(get_stale_threshold_days),
    session=Depends(get_session)
):
    registry_data = get_registry_data(session)

    sources = {}
    total_servers = 0
    total_stale_servers = 0

    for server in registry_data:
        source = server["registry_source"]
        last_scanned = datetime.fromisoformat(server["last_scanned"])

        days_since_last_scan = calculate_days_since_last_scan(last_scanned)
        is_stale = days_since_last_scan > stale_threshold_days

        if source not in sources:
            sources[source] = {
                "total_servers": 0,
                "days_since_last_scan": [],
                "stale_servers": 0,
                "oldest_server_seen_at": last_scanned
            }

        sources[source]["total_servers"] += 1
        sources[source]["days_since_last_scan"].append(days_since_last_scan)
        if is_stale:
            sources[source]["stale_servers"] += 1
        if last_scanned < sources[source]["oldest_server_seen_at"]:
            sources[source]["oldest_server_seen_at"] = last_scanned

        total_servers += 1
        if is_stale:
            total_stale_servers += 1

    report_sources = []
    alerts = []

    for source, data in sources.items():
        avg_days = mean(data["days_since_last_scan"])
        max_days = max(data["days_since_last_scan"])
        stale_pct = (data["stale_servers"] / data["total_servers"]) * 100

        report_sources.append(
            SourceFreshness(
                source=source,
                total_servers=data["total_servers"],
                avg_days_since_last_scan=avg_days,
                max_days_since_last_scan=max_days,
                stale_servers_pct=stale_pct,
                oldest_server_seen_at=data["oldest_server_seen_at"].isoformat()
            )
        )

        if stale_pct > 0:
            alerts.append(
                f"Source '{source}' has {data['stale_servers']} stale servers "
                f"({stale_pct:.1f}%) - oldest scan was {data['oldest_server_seen_at'].isoformat()}"
            )

    overall_stale_pct = (total_stale_servers / total_servers) * 100 if total_servers > 0 else 0

    return FreshnessReport(
        sources=report_sources,
        overall_stale_pct=overall_stale_pct,
        alerts=alerts
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import MCPServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    MCPServerRegistry.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    test_session = TestSessionLocal()
    test_data = [
        MCPServerRegistry(
            registry_source="source1",
            last_scanned=(datetime.utcnow() - timedelta(days=1)).isoformat()
        ),
        MCPServerRegistry(
            registry_source="source1",
            last_scanned=(datetime.utcnow() - timedelta(days=5)).isoformat()
        ),
        MCPServerRegistry(
            registry_source="source2",
            last_scanned=(datetime.utcnow() - timedelta(days=40)).isoformat()
        ),
        MCPServerRegistry(
            registry_source="source2",
            last_scanned=(datetime.utcnow() - timedelta(days=45)).isoformat()
        ),
        MCPServerRegistry(
            registry_source="source3",
            last_scanned=(datetime.utcnow() - timedelta(days=20)).isoformat()
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/registry/freshness-report?stale_threshold_days=30")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["sources"], list)
    assert 0 <= data["overall_stale_pct"] <= 100
    assert isinstance(data["alerts"], list)

    print("PASS")