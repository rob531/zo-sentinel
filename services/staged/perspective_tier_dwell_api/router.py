from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot

try:
    from .logic import get_tier_dwell
except ImportError:
    try:
        from .logic import get_tier_dwell_data as get_tier_dwell
    except ImportError:
        try:
            from .logic import get_tier_dwell_metrics as get_tier_dwell
        except ImportError:
            try:
                from .logic import get_perspective_tier_dwell as get_tier_dwell
            except ImportError:
                from .logic import compute_tier_dwell as get_tier_dwell


class TierTransition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int


class TierDwellResponse(BaseModel):
    perspective_id: int
    total_events: int
    unseen_events: int
    transitions: List[TierTransition]
    avg_dwell_days: float
    servers_affected: int


router = APIRouter(prefix="/api", tags=["perspective-tier-dwell"])


@router.get(
    "/perspectives/{perspective_id}/tier-dwell",
    response_model=TierDwellResponse,
)
def perspective_tier_dwell(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> TierDwellResponse:
    return get_tier_dwell(session, perspective_id)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    db = testing_session()
    try:
        base_time = datetime(2025, 1, 1, 12, 0, 0)
        db.add_all(
            [
                PerspectiveEvent(
                    perspective_id=1,
                    server_id="server-a",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                    created_at=base_time,
                ),
                PerspectiveEvent(
                    perspective_id=1,
                    server_id="server-a",
                    change_type="tier_change",
                    old_tier="medium",
                    new_tier="high",
                    seen=False,
                    created_at=base_time + timedelta(days=2),
                ),
                PerspectiveEvent(
                    perspective_id=1,
                    server_id="server-b",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="medium",
                    seen=False,
                    created_at=base_time + timedelta(days=1),
                ),
            ]
        )
        db.add(
            PerspectiveSnapshot(
                perspective_id=1,
                membership={"servers": ["server-a", "server-b"]},
                taken_at=base_time + timedelta(days=3),
            )
        )
        db.commit()
    finally:
        db.close()

    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/1/tier-dwell")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_events"] == 3, payload
        assert len(payload["transitions"]) >= 1, payload

    print("PASS")