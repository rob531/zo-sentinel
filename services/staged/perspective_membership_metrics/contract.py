from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import Perspective, PerspectiveMembership
from app.cache import cache
from sqlalchemy.orm import Session
from sqlalchemy import func

app = FastAPI()

class MemberMetric(BaseModel):
    member_id: int
    score: float
    last_updated: datetime

class PerspectiveMetrics(BaseModel):
    member_count: int
    last_updated: datetime
    top_members: List[MemberMetric]

def get_perspective_metrics(perspective_id: int, db: Session = Depends(get_session)) -> PerspectiveMetrics:
    # Check if perspective exists
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Get basic metrics
    member_count = db.query(func.count(PerspectiveMembership.id)).filter(
        PerspectiveMembership.perspective_id == perspective_id
    ).scalar()

    last_updated = db.query(func.max(PerspectiveMembership.updated_at)).filter(
        PerspectiveMembership.perspective_id == perspective_id
    ).scalar()

    # Get top members (top 5 by score)
    top_members = db.query(
        PerspectiveMembership.member_id,
        PerspectiveMembership.score,
        PerspectiveMembership.updated_at
    ).filter(
        PerspectiveMembership.perspective_id == perspective_id
    ).order_by(
        PerspectiveMembership.score.desc()
    ).limit(5).all()

    return PerspectiveMetrics(
        member_count=member_count or 0,
        last_updated=last_updated or datetime.min,
        top_members=[MemberMetric(**member._asdict()) for member in top_members]
    )

@app.get("/api/perspectives/{perspective_id}/metrics", response_model=PerspectiveMetrics)
@cache(key_prefix="perspective_metrics", ttl=300)
async def perspective_metrics(perspective_id: int, db: Session = Depends(get_session)):
    return get_perspective_metrics(perspective_id, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Create test data
    with TestSession() as session:
        perspective = Perspective(name="Test Perspective")
        session.add(perspective)
        session.commit()

        for i in range(1, 6):
            membership = PerspectiveMembership(
                perspective_id=perspective.id,
                member_id=i,
                score=i * 10.0,
                updated_at=datetime.now()
            )
            session.add(membership)
        session.commit()

    # Test valid perspective
    response = client.get(f"/api/perspectives/{perspective.id}/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["member_count"] == 5
    assert len(data["top_members"]) == 5
    assert data["top_members"][0]["score"] == 50.0

    # Test invalid perspective
    response = client.get("/api/perspectives/999999/metrics")
    assert response.status_code == 404

    # Test cache
    response1 = client.get(f"/api/perspectives/{perspective.id}/metrics")
    response2 = client.get(f"/api/perspectives/{perspective.id}/metrics")
    assert response1.json() == response2.json()

    print("PASS")