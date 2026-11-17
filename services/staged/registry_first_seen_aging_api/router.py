from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_aging


router = APIRouter(prefix="/api", tags=["registry"])


class RegistryAgingBucket(BaseModel):
    bucket: str
    min_days: int = Field(ge=0)
    max_days: Optional[int] = Field(default=None, ge=0)
    count: int = Field(ge=0)


class RegistrySourceCount(BaseModel):
    registry_source: Optional[str] = None
    count: int = Field(ge=0)


class NeverScannedSummary(BaseModel):
    count: int = Field(ge=0)
    pct: float = Field(ge=0, le=100)


class RegistryAgingResponse(BaseModel):
    generated_at: datetime
    total_servers: int = Field(ge=0)
    buckets: List[RegistryAgingBucket]
    never_scanned: NeverScannedSummary
    by_source: List[RegistrySourceCount]
    stale_30d: int = Field(ge=0)


@router.get("/registry/aging", response_model=RegistryAgingResponse)
def registry_aging(session: Session = Depends(get_session)) -> RegistryAgingResponse:
    return get_registry_aging(session)


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
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "contract-01",
            "first_seen": now - timedelta(days=1),
            "last_seen": now - timedelta(days=1),
            "last_scanned": None,
            "scan_count": 0,
            "registry_source": "contract",
            "risk_tier": "medium",
        },
        {
            "server_id": "contract-02",
            "first_seen": now - timedelta(days=10),
            "last_seen": now - timedelta(days=10),
            "last_scanned": None,
            "scan_count": 0,
            "registry_source": "contract",
            "risk_tier": "medium",
        },
        {
            "server_id": "contract-03",
            "first_seen": now - timedelta(days=40),
            "last_seen": now - timedelta(days=40),
            "last_scanned": now - timedelta(days=40),
            "scan_count": 1,
            "registry_source": "contract",
            "risk_tier": "medium",
        },
        {
            "server_id": "contract-04",
            "first_seen": now - timedelta(days=100),
            "last_seen": now - timedelta(days=40),
            "last_scanned": now - timedelta(days=40),
            "scan_count": 1,
            "registry_source": "contract",
            "risk_tier": "medium",
        },
        {
            "server_id": "contract-05",
            "first_seen": now - timedelta(days=400),
            "last_seen": now - timedelta(days=400),
            "last_scanned": now - timedelta(days=400),
            "scan_count": 1,
            "registry_source": "contract",
            "risk_tier": "medium",
        },
        {
            "server_id": "contract-06",
            "first_seen": now - timedelta(days=20),
            "last_seen": now - timedelta(days=1),
            "last_scanned": now - timedelta(days=1),
            "scan_count": 2,
            "registry_source": "contract",
            "risk_tier": "medium",
        },
    ]
    test_session.execute(McpServerRegistry.__table__.insert(), rows)
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/aging")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 6
        assert payload["never_scanned"]["count"] == 2
        assert {
            "0-7",
            "8-30",
            "31-90",
            "91-365",
            "365+",
        }.issubset({item["bucket"] for item in payload["buckets"]})

    test_session.close()
    test_engine.dispose()
    print("PASS")