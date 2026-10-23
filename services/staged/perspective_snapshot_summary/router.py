from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import PerspectiveSummaryResponse, get_perspective_summary

router = APIRouter(prefix="/api", tags=["perspective_snapshot_summary"])


@router.get(
    "/perspectives/{perspective_id}/summary",
    response_model=PerspectiveSummaryResponse,
)
def perspective_snapshot_summary(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> PerspectiveSummaryResponse:
    return get_perspective_summary(perspective_id, session)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import Perspective, PerspectiveSnapshot

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(bind=engine)
    PerspectiveSnapshot.__table__.create(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    now = datetime.utcnow()
    with TestSessionLocal() as session:
        session.add_all(
            [
                Perspective(
                    id="1",
                    org_id="10",
                    name="Perspective One",
                    description="First test perspective",
                    facet_filters={"risk_tier": ["critical", "high"]},
                    created_by="100",
                    created_at=now,
                    updated_at=now,
                ),
                Perspective(
                    id="2",
                    org_id="20",
                    name="Perspective Two",
                    description="Second test perspective",
                    facet_filters={"risk_tier": ["low"]},
                    created_by="200",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.add_all(
            [
                PerspectiveSnapshot(
                    id=1,
                    perspective_id="1",
                    taken_at=now,
                    membership={"critical": ["srv-a", "srv-b"], "high": ["srv-c"]},
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id="2",
                    taken_at=now + timedelta(seconds=1),
                    membership={"low": ["srv-x", "srv-y"]},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/1/summary")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["name"] == "Perspective One", result
        assert result["total_servers"] > 0, result

    print("PASS")