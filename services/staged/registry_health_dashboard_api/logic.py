from datetime import datetime
from typing import Annotated

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistryHealthResponse(BaseModel):
    source: str
    server_count: int
    avg_scan_count: float
    first_seen_oldest: str | None
    last_scanned_newest: str | None
    tier_breakdown: dict[str, int]


def get_registry_health(session: Session = Depends(get_session)) -> list[RegistryHealthResponse]:
    """
    Compute per-registry-source freshness and scan-coverage stats from McpServerRegistry.
    """
    results = session.query(
        McpServerRegistry.registry_source,
        func.count(McpServerRegistry.server_id).label('server_count'),
        func.avg(McpServerRegistry.scan_count).label('avg_scan_count'),
        func.min(McpServerRegistry.first_seen).label('first_seen_oldest'),
        func.max(McpServerRegistry.last_scanned).label('last_scanned_newest'),
    ).group_by(McpServerRegistry.registry_source).all()

    health_stats = []
    for row in results:
        tier_breakdown_query = session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label('count')
        ).filter(
            McpServerRegistry.registry_source == row.registry_source
        ).group_by(McpServerRegistry.risk_tier).all()

        tier_breakdown = {tier: count for tier, count in tier_breakdown_query}

        health_stats.append(RegistryHealthResponse(
            source=row.registry_source,
            server_count=row.server_count,
            avg_scan_count=float(row.avg_scan_count) if row.avg_scan_count else 0.0,
            first_seen_oldest=row.first_seen_oldest.isoformat() if row.first_seen_oldest else None,
            last_scanned_newest=row.last_scanned_newest.isoformat() if row.last_scanned_newest else None,
            tier_breakdown=tier_breakdown,
        ))

    return health_stats


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    db = TestingSessionLocal()
    sources = ["registry_a", "registry_b", "registry_c"]

    for i, source in enumerate(sources):
        for j in range(5):
            server = McpServerRegistry(
                server_id=f"srv_{i}_{j}",
                registry_source=source,
                name=f"Server {i}-{j}",
                risk_tier=["low", "medium", "high", "critical"][j % 4],
                scan_count=10 + j,
                first_seen=datetime(2024, 1, 1),
                last_scanned=datetime(2024, 6, 1),
            )
            db.add(server)

    db.commit()
    db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/registry/health")
    def health_endpoint():
        return get_registry_health(next(override_get_session()))

    client = TestClient(app)
    response = client.get("/api/registry/health")
    assert response.status_code == 200

    data = response.json()
    assert len(data) == 3
    for source_data in data:
        assert source_data["server_count"] == 5

    print("PASS")