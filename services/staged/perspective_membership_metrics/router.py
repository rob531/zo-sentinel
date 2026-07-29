from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective, PerspectiveMembership
from app.cache import cache
from datetime import datetime
from typing import List, Dict, Optional

router = APIRouter()

def get_perspective_metrics(perspective_id: int, session: Session = Depends(get_session)) -> Dict:
    perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    member_count = session.query(PerspectiveMembership).filter(
        PerspectiveMembership.perspective_id == perspective_id
    ).count()

    last_updated = perspective.updated_at or perspective.created_at

    top_members = session.query(
        PerspectiveMembership.member_id,
        PerspectiveMembership.score
    ).filter(
        PerspectiveMembership.perspective_id == perspective_id
    ).order_by(
        PerspectiveMembership.score.desc()
    ).limit(5).all()

    top_members_list = [{
        "member_id": member.member_id,
        "score": member.score
    } for member in top_members]

    return {
        "member_count": member_count,
        "last_updated": last_updated.isoformat(),
        "top_members": top_members_list
    }

@router.get("/api/perspectives/{perspective_id}/metrics")
@cache(key_prefix="perspective_metrics_")
async def get_metrics(perspective_id: int, session: Session = Depends(get_session)) -> Dict:
    return get_perspective_metrics(perspective_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Create test data
    with TestSessionLocal() as session:
        perspective = Perspective(
            name="Test Perspective",
            description="Test Description",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        session.add(perspective)
        session.commit()

        for i in range(5):
            membership = PerspectiveMembership(
                perspective_id=perspective.id,
                member_id=f"member_{i}",
                score=i * 10
            )
            session.add(membership)
        session.commit()

    client = TestClient(app)

    # Test valid perspective ID
    response = client.get(f"/api/perspectives/{perspective.id}/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["member_count"] == 5
    assert data["last_updated"] is not None
    assert len(data["top_members"]) == 5

    # Test invalid perspective ID
    response = client.get("/api/perspectives/999999/metrics")
    assert response.status_code == 404

    # Test cache hit
    response1 = client.get(f"/api/perspectives/{perspective.id}/metrics")
    response2 = client.get(f"/api/perspectives/{perspective.id}/metrics")
    assert response1.json() == response2.json()

    print("PASS")