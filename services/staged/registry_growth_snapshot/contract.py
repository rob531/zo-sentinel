from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, CadenceJobRun
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session
import json

router = APIRouter(prefix="/api/registry")

class SourceStats(BaseModel):
    registry_source: str
    count: int
    pct: float

class GrowthSeriesPoint(BaseModel):
    date: str
    total_count: int

class RegistryGrowthSnapshot(BaseModel):
    snapshot_at: str
    daily_new: int
    weekly_new: int
    monthly_new: int
    total: int
    by_source: List[SourceStats]
    growth_series: List[GrowthSeriesPoint]
    avg_daily_growth_rate: float

def get_registry_growth_snapshot(db: Session = Depends(get_session)) -> RegistryGrowthSnapshot:
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    # Calculate daily new servers
    daily_new = db.query(McpServerRegistry).filter(
        func.date(McpServerRegistry.first_seen) == today
    ).count()

    # Calculate weekly new servers
    weekly_new = db.query(McpServerRegistry).filter(
        func.date(McpServerRegistry.first_seen) >= week_ago
    ).count()

    # Calculate monthly new servers
    monthly_new = db.query(McpServerRegistry).filter(
        func.date(McpServerRegistry.first_seen) >= month_ago
    ).count()

    # Calculate total servers
    total = db.query(McpServerRegistry).count()

    # Calculate by source stats
    source_counts = db.query(
        McpServerRegistry.registry_source,
        func.count(McpServerRegistry.id).label('count')
    ).group_by(McpServerRegistry.registry_source).all()

    by_source = []
    for source, count in source_counts:
        pct = (count / total) * 100 if total > 0 else 0
        by_source.append(SourceStats(
            registry_source=source,
            count=count,
            pct=round(pct, 2)
        ))

    # Calculate growth series
    growth_series = []
    for day in range(30):
        date = today - timedelta(days=day)
        count = db.query(CadenceJobRun).filter(
            CadenceJobRun.job == 'registry_growth_snapshot',
            func.date(CadenceJobRun.created_at) == date
        ).scalar()

        if count is not None:
            growth_series.append(GrowthSeriesPoint(
                date=date.isoformat(),
                total_count=count
            ))

    # Calculate average daily growth rate
    if len(growth_series) >= 2:
        first = growth_series[-1].total_count
        last = growth_series[0].total_count
        days = len(growth_series) - 1
        avg_daily_growth_rate = ((last - first) / days) / first * 100 if first != 0 else 0
    else:
        avg_daily_growth_rate = 0

    return RegistryGrowthSnapshot(
        snapshot_at=datetime.utcnow().isoformat(),
        daily_new=daily_new,
        weekly_new=weekly_new,
        monthly_new=monthly_new,
        total=total,
        by_source=by_source,
        growth_series=growth_series,
        avg_daily_growth_rate=round(avg_daily_growth_rate, 2)
    )

router.get("/growth-snapshot", response_model=RegistryGrowthSnapshot)(get_registry_growth_snapshot)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependencies for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        test_servers = [
            McpServerRegistry(
                id=1,
                registry_source="source1",
                first_seen=datetime.utcnow().date() - timedelta(days=1)
            ),
            McpServerRegistry(
                id=2,
                registry_source="source1",
                first_seen=datetime.utcnow().date() - timedelta(days=2)
            ),
            McpServerRegistry(
                id=3,
                registry_source="source2",
                first_seen=datetime.utcnow().date() - timedelta(days=3)
            ),
            McpServerRegistry(
                id=4,
                registry_source="source2",
                first_seen=datetime.utcnow().date() - timedelta(days=4)
            ),
            McpServerRegistry(
                id=5,
                registry_source="source2",
                first_seen=datetime.utcnow().date() - timedelta(days=5)
            )
        ]
        session.add_all(test_servers)
        session.commit()

    client = TestClient(app)
    response = client.get("/api/registry/growth-snapshot")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["by_source"]) == 2

    print("PASS")