from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from pydantic import BaseModel
import requests

router = APIRouter()

class LaneReport(BaseModel):
    sources: List[str]
    count: int
    scored: int
    never_scored: int
    newest_created_at: Optional[datetime]
    flat: bool

class LaneSummary(BaseModel):
    A: LaneReport
    B: LaneReport
    C: LaneReport
    UNMAPPED: LaneReport

def get_lane_for_source(source: str) -> str:
    if source.startswith('http://') or source.startswith('https://'):
        if 'github.com' in source:
            return 'B'
        elif 'npmjs.com' in source or 'pypi.org' in source:
            return 'C'
    return 'UNMAPPED'

def get_previous_lane_counts() -> Dict[str, int]:
    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={'query': 'SELECT lane, count FROM registry_growth_snapshots ORDER BY snapshot_at DESC LIMIT 1'}
        )
        response.raise_for_status()
        data = response.json()
        return {row['lane']: row['count'] for row in data['rows']}
    except Exception as e:
        print(f"Warning: Could not fetch previous lane counts: {e}")
        return {}

@router.get("/registry-source-lane-report", response_model=LaneSummary)
async def registry_source_lane_report(db: Session = Depends(get_session)):
    # Get all registry sources
    registries = db.query(MCPServerRegistry).all()

    # Initialize lane reports
    lane_reports = {
        'A': {'sources': [], 'count': 0, 'scored': 0, 'never_scored': 0, 'newest_created_at': None},
        'B': {'sources': [], 'count': 0, 'scored': 0, 'never_scored': 0, 'newest_created_at': None},
        'C': {'sources': [], 'count': 0, 'scored': 0, 'never_scored': 0, 'newest_created_at': None},
        'UNMAPPED': {'sources': [], 'count': 0, 'scored': 0, 'never_scored': 0, 'newest_created_at': None}
    }

    # Get previous lane counts for flat flag
    previous_counts = get_previous_lane_counts()

    # Categorize registries by lane
    for registry in registries:
        lane = get_lane_for_source(registry.source)
        lane_reports[lane]['sources'].append(registry.source)
        lane_reports[lane]['count'] += 1

        # Check if scored
        has_scores = db.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.registry_id == registry.id
        ).first() is not None

        if has_scores:
            lane_reports[lane]['scored'] += 1
        else:
            lane_reports[lane]['never_scored'] += 1

        # Update newest_created_at
        if registry.created_at and (lane_reports[lane]['newest_created_at'] is None or registry.created_at > lane_reports[lane]['newest_created_at']):
            lane_reports[lane]['newest_created_at'] = registry.created_at

    # Convert to LaneReport objects and add flat flags
    result = {}
    for lane, report in lane_reports.items():
        flat = previous_counts.get(lane, 0) == report['count']
        result[lane] = LaneReport(
            sources=report['sources'],
            count=report['count'],
            scored=report['scored'],
            never_scored=report['never_scored'],
            newest_created_at=report['newest_created_at'],
            flat=flat
        )

    return LaneSummary(**result)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    with TestSession() as session:
        session.add_all([
            MCPServerRegistry(
                id=1,
                source="http://github.com/owner/repo1",
                created_at=datetime(2023, 1, 1)
            ),
            MCPServerRegistry(
                id=2,
                source="http://github.com/owner/repo2",
                created_at=datetime(2023, 1, 2)
            ),
            MCPServerRegistry(
                id=3,
                source="https://npmjs.com/package1",
                created_at=datetime(2023, 1, 3)
            ),
            MCPServerRegistry(
                id=4,
                source="https://pypi.org/package1",
                created_at=datetime(2023, 1, 4)
            ),
            MCPServerRegistry(
                id=5,
                source="http://example.com/source1",
                created_at=datetime(2023, 1, 5)
            ),
            MCPServerRegistry(
                id=6,
                source="http://example.com/source2",
                created_at=datetime(2023, 1, 6)
            ),
            MCPLLMAxisScores(
                registry_id=1,
                overall_risk=0.5,
                auth_strength=0.6,
                capability_breadth=0.7,
                data_sensitivity=0.8,
                network_egress=0.9,
                maintainer_trust=0.4,
                exploit_surface=0.3
            ),
            MCPLLMAxisScores(
                registry_id=3,
                overall_risk=0.2,
                auth_strength=0.3,
                capability_breadth=0.4,
                data_sensitivity=0.5,
                network_egress=0.6,
                maintainer_trust=0.7,
                exploit_surface=0.8
            )
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/registry-source-lane-report")
    assert response.status_code == 200

    data = response.json()
    assert data["A"]["count"] == 0  # No directory aggregators in test data
    assert data["B"]["count"] == 2
    assert data["B"]["scored"] == 1
    assert data["B"]["never_scored"] == 1
    assert data["C"]["count"] == 2
    assert data["C"]["scored"] == 1
    assert data["C"]["never_scored"] == 1
    assert data["UNMAPPED"]["count"] == 2
    assert data["UNMAPPED"]["scored"] == 0
    assert data["UNMAPPED"]["never_scored"] == 2

    print("PASS")