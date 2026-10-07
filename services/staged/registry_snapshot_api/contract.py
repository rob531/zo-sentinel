from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/registry", tags=["registry"])


class SnapshotTotals(BaseModel):
    servers: int
    avg_trust: float


class SnapshotTierCounts(BaseModel):
    TRUSTED_GENERAL: int = 0
    TRUSTED_CREDENTIALED: int = 0
    UNTRUSTED: int = 0
    UNKNOWN: int = 0


class SnapshotSourceCounts(BaseModel):
    npm: int = 0
    pypi: int = 0
    github: int = 0
    direct: int = 0


class SnapshotHistoryEntry(BaseModel):
    date: str
    by_tier: SnapshotTierCounts


class RegistrySnapshotResponse(BaseModel):
    days: int
    generated_at: str
    totals: SnapshotTotals
    by_tier: SnapshotTierCounts
    by_source: SnapshotSourceCounts
    history: list[SnapshotHistoryEntry]


def compute_snapshot(session: Session, days: int) -> RegistrySnapshotResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    base_query = select(McpServerRegistry).where(
        McpServerRegistry.first_seen >= cutoff
    )
    results = session.execute(base_query).scalars().all()
    
    totals_servers = len(results)
    avg_trust = sum(r.trust_score or 0 for r in results) / totals_servers if totals_servers > 0 else 0.0
    
    by_tier: dict[str, int] = {"TRUSTED_GENERAL": 0, "TRUSTED_CREDENTIALED": 0, "UNTRUSTED": 0, "UNKNOWN": 0}
    by_source: dict[str, int] = {"npm": 0, "pypi": 0, "github": 0, "direct": 0}
    
    for r in results:
        tier = r.risk_tier or "UNKNOWN"
        if tier in by_tier:
            by_tier[tier] += 1
        source = r.registry_source or "direct"
        if source in by_source:
            by_source[source] += 1
    
    history: dict[str, dict[str, int]] = {}
    for r in results:
        if r.first_seen:
            d = r.first_seen.date().isoformat()
            if d not in history:
                history[d] = {"TRUSTED_GENERAL": 0, "TRUSTED_CREDENTIALED": 0, "UNTRUSTED": 0, "UNKNOWN": 0}
            tier = r.risk_tier or "UNKNOWN"
            if tier in history[d]:
                history[d][tier] += 1
    
    history_entries = [
        SnapshotHistoryEntry(date=k, by_tier=SnapshotTierCounts(**v))
        for k, v in sorted(history.items())
    ]
    
    return RegistrySnapshotResponse(
        days=days,
        generated_at=datetime.utcnow().isoformat(),
        totals=SnapshotTotals(servers=totals_servers, avg_trust=round(avg_trust, 3)),
        by_tier=SnapshotTierCounts(**by_tier),
        by_source=SnapshotSourceCounts(**by_source),
        history=history_entries,
    )


@router.get("/snapshot", response_model=RegistrySnapshotResponse)
def get_registry_snapshot(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> RegistrySnapshotResponse:
    return compute_snapshot(session, days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session
    from app.models import McpServerRegistry, Base as AppBase
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    AppBase.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    db = TestingSessionLocal()
    now = datetime.utcnow()
    for i in range(5):
        reg = McpServerRegistry(
            server_id=f"srv_{i}",
            name=f"TestServer{i}",
            registry_source=["npm", "pypi", "github", "direct"][i % 4],
            risk_tier=["TRUSTED_GENERAL", "TRUSTED_CREDENTIALED", "UNTRUSTED", "UNKNOWN"][i % 4],
            trust_score=0.5 + (i * 0.1),
            first_seen=now - timedelta(days=i),
            url=f"https://example.com/{i}",
        )
        db.add(reg)
    db.commit()
    db.close()
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    response = client.get("/api/registry/snapshot?days=30")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "history" in data
    assert isinstance(data["history"], list)
    
    by_tier_keys = set(data["by_tier"].keys())
    expected_tier_keys = {"TRUSTED_GENERAL", "TRUSTED_CREDENTIALED", "UNTRUSTED", "UNKNOWN"}
    assert by_tier_keys == expected_tier_keys, f"Expected {expected_tier_keys}, got {by_tier_keys}"
    
    assert data["totals"]["servers"] > 0, f"Expected servers > 0, got {data['totals']['servers']}"
    
    print("PASS")