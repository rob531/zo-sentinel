from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry


class RegistryOverviewResponse(BaseModel):
    total: int
    by_tier: Dict[str, int]
    by_source: Dict[str, int]
    unassessed: int
    fresh_7d: int


def endpoint(session: Session = Depends(get_session)) -> Dict[str, Any]:
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    total = session.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    tier_rows = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.risk_tier).all()
    by_tier = {tier: count for tier, count in tier_rows if tier is not None}

    source_rows = session.query(
        McpServerRegistry.registry_source,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.registry_source).all()
    by_source = {src: count for src, count in source_rows if src is not None}

    unassessed = session.query(func.count(McpServerRegistry.server_id)).filter(
        or_(
            McpServerRegistry.verdict == None,
            McpServerRegistry.verdict == "INSUFFICIENT"
        )
    ).scalar() or 0

    fresh_7d = session.query(func.count(McpServerRegistry.server_id)).filter(
        McpServerRegistry.last_assessed != None,
        McpServerRegistry.last_assessed >= seven_days_ago
    ).scalar() or 0

    return {
        "total": total,
        "by_tier": by_tier,
        "by_source": by_source,
        "unassessed": unassessed,
        "fresh_7d": fresh_7d,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    McpServerRegistry.__table__.create(engine)

    session = SessionLocal()
    now = datetime.utcnow()
    session.add_all([
        McpServerRegistry(server_id="s1", risk_tier="CRITICAL", registry_source="npm", verdict="APPROVED", last_assessed=now - timedelta(days=10)),
        McpServerRegistry(server_id="s2", risk_tier="HIGH", registry_source="github", verdict="REJECTED", last_assessed=now - timedelta(days=20)),
        McpServerRegistry(server_id="s3", risk_tier="MEDIUM", registry_source="pypi", verdict="APPROVED", last_assessed=now - timedelta(days=30)),
        McpServerRegistry(server_id="s4", risk_tier="LOW", registry_source="docker", verdict=None, last_assessed=now),
    ])
    session.commit()
    session.close()

    def override_get_session():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/registry/overview")
    def get_overview(session: Session = Depends(get_session)):
        return endpoint(session)

    client = TestClient(app)
    response = client.get("/api/registry/overview")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert data["total"] >= 4, f"Expected total >= 4, got {data['total']}"
    assert "CRITICAL" in data["by_tier"], f"Expected CRITICAL in by_tier, got {data['by_tier']}"
    assert "HIGH" in data["by_tier"], f"Expected HIGH in by_tier, got {data['by_tier']}"

    print("PASS")