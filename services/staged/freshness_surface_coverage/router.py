from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

try:
    from .logic import get_freshness_surfaces as _get_freshness_surfaces
except ImportError:
    from .logic import get_freshness_surface_coverage as _get_freshness_surfaces

router = APIRouter(prefix="/api", tags=["freshness-surface-coverage"])


@router.get("/freshness/surfaces")
def get_freshness_surfaces(session: Session = Depends(get_session)):
    return _get_freshness_surfaces(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    now = datetime.utcnow()
    test_session.add_all(
        [
            FreshnessStat(
                surface="scanner",
                payload={"kind": "scan"},
                computed_at=now,
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="scorer",
                payload={"kind": "score"},
                computed_at=now,
                compute_seconds=0.5,
            ),
            FreshnessStat(
                surface="intel",
                payload={"kind": "intel"},
                computed_at=now - timedelta(hours=2),
                compute_seconds=1.0,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/freshness/surfaces")
    response.raise_for_status()
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert sum(1 for surface in body["surfaces"] if surface["stale"]) == 1
    assert body["counts"]["total"] == 3
    assert body["counts"]["stale"] == 1
    assert body["counts"]["fresh"] == 2
    print("PASS")