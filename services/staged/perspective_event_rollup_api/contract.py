from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective
from typing import List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel

class PerspectiveEventRollup(BaseModel):
    perspective_id: int
    event_count: int
    last_event_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

class PerspectiveEventRollupResponse(BaseModel):
    data: List[PerspectiveEventRollup]
    total: int

def get_perspective_event_rollup(
    session: Session = Depends(get_session),
    org_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0
) -> PerspectiveEventRollupResponse:
    query = session.query(
        Perspective.id.label('perspective_id'),
        Perspective.created_at,
        Perspective.updated_at
    )

    if org_id is not None:
        query = query.filter(Perspective.org_id == org_id)

    if start_date is not None:
        query = query.filter(Perspective.created_at >= start_date)

    if end_date is not None:
        query = query.filter(Perspective.created_at <= end_date)

    subquery = query.subquery()

    result = session.query(
        subquery.c.perspective_id,
        subquery.c.created_at,
        subquery.c.updated_at,
        subquery.c.perspective_id.label('event_count'),
        subquery.c.updated_at.label('last_event_at')
    ).group_by(
        subquery.c.perspective_id,
        subquery.c.created_at,
        subquery.c.updated_at
    ).limit(limit).offset(offset).all()

    total = session.query(
        subquery.c.perspective_id
    ).group_by(
        subquery.c.perspective_id
    ).count()

    return PerspectiveEventRollupResponse(
        data=[PerspectiveEventRollup(**row._asdict()) for row in result],
        total=total
    )

app = FastAPI()

@app.get("/perspective_event_rollup", response_model=PerspectiveEventRollupResponse)
async def perspective_event_rollup(
    org_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session)
):
    return get_perspective_event_rollup(
        session=session,
        org_id=org_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset
    )

if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.models import Base

    test_db_url = "sqlite:///:memory:"
    engine = create_engine(test_db_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def get_test_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = get_test_session

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/perspective_event_rollup")
    assert response.status_code == 200
    print("PASS")