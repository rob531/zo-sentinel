from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


def get_registry_freshness(days: int, session: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    result = (
        session.query(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("server_count"),
            func.avg(
                func.julianday(now) - func.julianday(McpServerRegistry.last_scanned)
            ).label("avg_age_days"),
            func.max(
                func.julianday(now) - func.julianday(McpServerRegistry.last_scanned)
            ).label("max_age_days"),
        )
        .group_by(McpServerRegistry.registry_source)
        .all()
    )
    sources = []
    for row in result:
        max_age = float(row.max_age_days) if row.max_age_days is not None else 0.0
        sources.append(
            {
                "source": row.registry_source,
                "server_count": row.server_count,
                "avg_age_days": round(float(row.avg_age_days), 2)
                if row.avg_age_days is not None
                else None,
                "max_age_days": round(max_age, 2),
                "freshness_status": "ACTIVE" if max_age < days else "STALE",
            }
        )
    return {"days": days, "sources": sources}


@router.get("/api/registry/freshness")
def registry_freshness(
    days: int = 30, session: Session = Depends(get_session)
) -> dict[str, Any]:
    return get_registry_freshness(days, session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    one_day_ago = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    five_days_ago = datetime(2025, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
    ten_days_ago = datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc)

    db.add(
        McpServerRegistry(
            server_id="srv1",
            registry_source="source_a",
            last_scanned=one_day_ago,
        )
    )
    db.add(
        McpServerRegistry(
            server_id="srv2",
            registry_source="source_a",
            last_scanned=five_days_ago,
        )
    )
    db.add(
        McpServerRegistry(
            server_id="srv3",
            registry_source="source_b",
            last_scanned=ten_days_ago,
        )
    )
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/api/registry/freshness?days=30")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["sources"]) >= 2, f"Expected >= 2 sources, got {len(data['sources'])}"
    for source in data["sources"]:
        assert source["freshness_status"] in ("ACTIVE", "STALE"), (
            f"Invalid freshness_status: {source['freshness_status']}"
        )

    print("PASS")