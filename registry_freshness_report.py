from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func, text
from app.db import get_session
from app.models import MCPServerRegistry
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class SourceStats(BaseModel):
    registry_source: str
    server_count: int
    oldest_seen: Optional[datetime]
    newest_scanned: Optional[datetime]
    avg_scans: float
    needs_scan: int
    stale: bool

class OverallStats(BaseModel):
    total: int
    sources_stale: int
    sources_never_scanned: int

class FreshnessReport(BaseModel):
    generated_at: datetime
    sources: List[SourceStats]
    overall: OverallStats

def get_stale_sources(session):
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    stale_query = session.query(
        MCPServerRegistry.registry_source,
        func.count(MCPServerRegistry.id).label('count')
    ).filter(
        (MCPServerRegistry.last_scanned < seven_days_ago) |
        (MCPServerRegistry.last_scanned.is_(None))
    ).group_by(
        MCPServerRegistry.registry_source
    ).all()
    return {row.registry_source: row.count for row in stale_query}

def get_never_scanned_sources(session):
    never_scanned_query = session.query(
        MCPServerRegistry.registry_source,
        func.count(MCPServerRegistry.id).label('count')
    ).filter(
        MCPServerRegistry.scan_count == 0
    ).group_by(
        MCPServerRegistry.registry_source
    ).all()
    return {row.registry_source: row.count for row in never_scanned_query}

@router.get("/registry/freshness-report", response_model=FreshnessReport)
async def get_freshness_report(session=Depends(get_session)):
    # Get overall stats
    total_servers = session.query(func.count(MCPServerRegistry.id)).scalar()

    stale_sources = get_stale_sources(session)
    never_scanned_sources = get_never_scanned_sources(session)

    # Get per-source stats
    sources_query = session.query(
        MCPServerRegistry.registry_source,
        func.count(MCPServerRegistry.id).label('server_count'),
        func.min(MCPServerRegistry.first_seen).label('oldest_seen'),
        func.max(MCPServerRegistry.last_scanned).label('newest_scanned'),
        func.avg(MCPServerRegistry.scan_count).label('avg_scans'),
        func.count(
            case(
                (MCPServerRegistry.scan_count == 0, 1),
                (MCPServerRegistry.last_scanned.is_(None), 1)
            )
        ).label('needs_scan')
    ).group_by(
        MCPServerRegistry.registry_source
    ).all()

    sources = []
    for row in sources_query:
        stale = stale_sources.get(row.registry_source, 0) > 0
        sources.append({
            "registry_source": row.registry_source,
            "server_count": row.server_count,
            "oldest_seen": row.oldest_seen,
            "newest_scanned": row.newest_scanned,
            "avg_scans": row.avg_scans,
            "needs_scan": row.needs_scan,
            "stale": stale
        })

    overall = {
        "total": total_servers,
        "sources_stale": sum(stale_sources.values()),
        "sources_never_scanned": sum(never_scanned_sources.values())
    }

    return {
        "generated_at": datetime.utcnow(),
        "sources": sources,
        "overall": overall
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    session = TestSession()
    from datetime import datetime, timedelta

    # Current time for reference
    now = datetime.utcnow()

    # Seed data with mixed scan states
    test_data = [
        MCPServerRegistry(
            registry_source="source1",
            first_seen=now - timedelta(days=10),
            last_scanned=now - timedelta(days=1),
            scan_count=5
        ),
        MCPServerRegistry(
            registry_source="source1",
            first_seen=now - timedelta(days=15),
            last_scanned=now - timedelta(days=5),
            scan_count=2
        ),
        MCPServerRegistry(
            registry_source="source2",
            first_seen=now - timedelta(days=20),
            last_scanned=None,
            scan_count=0
        ),
        MCPServerRegistry(
            registry_source="source2",
            first_seen=now - timedelta(days=25),
            last_scanned=now - timedelta(days=8),
            scan_count=0
        ),
        MCPServerRegistry(
            registry_source="source3",
            first_seen=now - timedelta(days=30),
            last_scanned=now - timedelta(days=2),
            scan_count=15
        ),
        MCPServerRegistry(
            registry_source="source3",
            first_seen=now - timedelta(days=35),
            last_scanned=now - timedelta(days=10),
            scan_count=0
        ),
        MCPServerRegistry(
            registry_source="source4",
            first_seen=now - timedelta(days=40),
            last_scanned=None,
            scan_count=0
        )
    ]

    session.add_all(test_data)
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/registry/freshness-report")
    assert response.status_code == 200
    report = response.json()

    # Verify stale and needs_scan flags
    for source in report["sources"]:
        if source["registry_source"] == "source2":
            assert source["stale"] is True
            assert source["needs_scan"] == 2
        elif source["registry_source"] == "source4":
            assert source["stale"] is True
            assert source["needs_scan"] == 1
        else:
            assert source["stale"] is False

    # Verify overall stats
    assert report["overall"]["sources_stale"] == 2  # source2 and source4
    assert report["overall"]["sources_never_scanned"] == 3  # source2 (2), source4 (1)

    print("PASS")