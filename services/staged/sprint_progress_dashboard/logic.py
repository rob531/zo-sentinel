from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

class SprintProgressSeriesItem(BaseModel):
    date: str
    proposed: int
    written: int
    rejected: int
    pending: int
    acceptance_rate: float
    avg_cycle_hours: float

class SprintProgressSummary(BaseModel):
    total_proposed: int
    total_written: int
    total_rejected: int
    total_pending: int
    avg_cycle_hours: float

class SprintProgressResponse(BaseModel):
    date_from: str
    date_to: str
    days: int
    summary: SprintProgressSummary
    series: List[SprintProgressSeriesItem]

def get_sprint_progress(
    db: Session = Depends(get_session),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> SprintProgressResponse:
    # Set default date range (last 7 days)
    if not date_from:
        date_from = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
    if not date_to:
        date_to = datetime.utcnow().strftime('%Y-%m-%d')

    try:
        start_date = datetime.strptime(date_from, '%Y-%m-%d')
        end_date = datetime.strptime(date_to, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    if end_date < start_date:
        raise HTTPException(status_code=400, detail="date_to must be after date_from")

    # Calculate days in range
    days = (end_date - start_date).days + 1

    # Query job runs for sprint/directive jobs in date range
    subquery = db.query(
        func.date(CadenceJobRun.finished_at).label('date'),
        func.count(CadenceJobRun.id).label('count'),
        func.sum(func.extract('epoch', (CadenceJobRun.finished_at - CadenceJobRun.started_at)) / 3600).label('total_hours')
    ).filter(
        and_(
            CadenceJobRun.finished_at >= start_date,
            CadenceJobRun.finished_at <= end_date,
            or_(
                CadenceJobRun.job.like('%sprint%'),
                CadenceJobRun.job.like('%directive%')
            )
        )
    ).group_by(
        func.date(CadenceJobRun.finished_at)
    ).subquery()

    # Get all dates in range (even with no jobs)
    date_series = []
    current_date = start_date
    while current_date <= end_date:
        date_series.append(current_date.strftime('%Y-%m-%d'))
        current_date += timedelta(days=1)

    # Get summary stats
    summary = db.query(
        func.sum(subquery.c.count).label('total_proposed'),
        func.sum(subquery.c.total_hours).label('total_hours')
    ).first()

    # Calculate summary
    total_proposed = summary.total_proposed or 0
    total_written = total_proposed  # Assuming all proposed are written
    total_rejected = 0  # Placeholder - adjust based on actual rejection logic
    total_pending = 0  # Placeholder - adjust based on actual pending logic
    avg_cycle_hours = (summary.total_hours / total_proposed) if total_proposed > 0 else 0

    # Get series data
    series = []
    for date in date_series:
        day_data = db.query(
            subquery.c.count,
            subquery.c.total_hours
        ).filter(
            subquery.c.date == date
        ).first()

        proposed = day_data.count if day_data else 0
        written = proposed  # Assuming all proposed are written
        rejected = 0  # Placeholder
        pending = 0  # Placeholder
        acceptance_rate = 1.0 if proposed > 0 else 0.0
        avg_hours = (day_data.total_hours / proposed) if (day_data and proposed > 0) else 0.0

        series.append(SprintProgressSeriesItem(
            date=date,
            proposed=proposed,
            written=written,
            rejected=rejected,
            pending=pending,
            acceptance_rate=acceptance_rate,
            avg_cycle_hours=avg_hours
        ))

    return SprintProgressResponse(
        date_from=date_from,
        date_to=date_to,
        days=days,
        summary=SprintProgressSummary(
            total_proposed=total_proposed,
            total_written=total_written,
            total_rejected=total_rejected,
            total_pending=total_pending,
            avg_cycle_hours=avg_cycle_hours
        ),
        series=series
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Create test session
    def get_test_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    # Create test app
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_test_session

    # Add test route
    from fastapi import APIRouter
    router = APIRouter()
    router.get("/api/dashboard/sprint-progress")(get_sprint_progress)
    test_app.include_router(router)

    # Create test data
    with Session(test_engine) as session:
        from datetime import datetime, timedelta
        from app.models import CadenceJobRun

        # Create test job runs
        now = datetime.utcnow()
        session.add_all([
            CadenceJobRun(
                job="sprint_1",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
                status="completed",
                detail="Test job 1"
            ),
            CadenceJobRun(
                job="directive_2",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2),
                status="completed",
                detail="Test job 2"
            ),
            CadenceJobRun(
                job="sprint_3",
                started_at=(now - timedelta(days=1)) - timedelta(hours=2),
                finished_at=(now - timedelta(days=1)) - timedelta(hours=1),
                status="completed",
                detail="Test job 3"
            )
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/api/dashboard/sprint-progress")
    assert response.status_code == 200

    data = response.json()
    assert len(data["series"]) >= 1
    assert isinstance(data["summary"]["total_proposed"], int)
    assert isinstance(data["summary"]["total_written"], int)
    assert isinstance(data["summary"]["total_rejected"], int)
    assert isinstance(data["summary"]["total_pending"], int)
    assert isinstance(data["summary"]["avg_cycle_hours"], (int, float))

    print("PASS")