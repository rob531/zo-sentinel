"""HTTP surface for registry freshness SLA reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_freshness_sla

router = APIRouter(prefix="/api/registry", tags=["registry-freshness-sla"])


class FreshnessSLAAgeBuckets(BaseModel):
    less_than_24h: int = 0
    one_to_seven_days: int = 0
    seven_to_thirty_days: int = 0
    over_thirty_days: int = 0


class FreshnessSLASource(BaseModel):
    registry_source: str
    total: int
    breach_count: int
    sla_breach_rate: float
    age_buckets: dict[str, int]
    oldest_unassessed_days: int


class FreshnessSLAResponse(BaseModel):
    sources: list[FreshnessSLASource]


@router.get("/freshness-sla", response_model=FreshnessSLAResponse)
def get_freshness_sla(db: Session = Depends(get_session)) -> FreshnessSLAResponse:
    result = get_registry_freshness_sla(db)
    return FreshnessSLAResponse(**result)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with TestingSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    registry_source="fresh-source",
                    last_assessed=now - timedelta(days=1),
                ),
                McpServerRegistry(
                    server_id="aging-server",
                    registry_source="aging-source",
                    last_assessed=now - timedelta(days=10),
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    registry_source="stale-source",
                    last_assessed=now - timedelta(days=60),
                ),
            ]
        )
        db.commit()

    response = TestClient(test_app).get("/api/registry/freshness-sla")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 3
    sources = {item["registry_source"]: item for item in payload["sources"]}
    assert sources["fresh-source"]["breach_count"] == 0
    assert sources["stale-source"]["breach_count"] == 1
    print("PASS")