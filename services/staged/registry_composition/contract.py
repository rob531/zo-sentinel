# services/staged/registry_composition/contract.py
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class TierInfo(BaseModel):
    risk_tier: str
    count: int
    percent: float


class SourceInfo(BaseModel):
    registry_source: str
    total: int
    tiers: List[TierInfo]


class CompositionResponse(BaseModel):
    total_servers: int
    sources: List[SourceInfo]


@router.get(
    "/registry/composition",
    response_model=CompositionResponse,
    summary="Breakdown of servers in the registry by source and risk tier",
)
def get_registry_composition(
    registry_source: Optional[str] = Query(None, description="Filter by registry source"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    db: Session = Depends(get_session),
) -> CompositionResponse:
    filters = []
    if registry_source:
        filters.append(McpServerRegistry.registry_source == registry_source)
    if risk_tier:
        filters.append(McpServerRegistry.risk_tier == risk_tier)

    # total number of servers (after filters)
    total_stmt = select(func.count()).select_from(McpServerRegistry)
    if filters:
        total_stmt = total_stmt.where(*filters)
    total = db.execute(total_stmt).scalar() or 0

    # aggregated counts per source / tier
    agg_stmt = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        func.count().label("cnt"),
    )
    if filters:
        agg_stmt = agg_stmt.where(*filters)
    agg_stmt = agg_stmt.group_by(
        McpServerRegistry.registry_source, McpServerRegistry.risk_tier
    )
    rows = db.execute(agg_stmt).all()

    # build hierarchical structure
    source_map = {}
    for src, tier, cnt in rows:
        source_map.setdefault(src, {"total": 0, "tiers": []})
        source_map[src]["total"] += cnt
        source_map[src]["tiers"].append((tier, cnt))

    sources: List[SourceInfo] = []
    for src, data in source_map.items():
        src_total = data["total"]
        tier_infos: List[TierInfo] = []
        for tier, cnt in data["tiers"]:
            percent = (cnt / src_total * 100) if src_total else 0.0
            tier_infos.append(
                TierInfo(risk_tier=tier, count=cnt, percent=round(percent, 2))
            )
        sources.append(
            SourceInfo(registry_source=src, total=src_total, tiers=tier_infos)
        )

    return CompositionResponse(total_servers=total, sources=sources)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_composition.contract)
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override for the test session
    def get_test_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    # Seed data: 2 sources × 3 tiers = 6 servers
    seed = [
        McpServerRegistry(
            server_id="s1",
            name="Server1",
            registry_source="srcA",
            risk_tier="Low",
        ),
        McpServerRegistry(
            server_id="s2",
            name="Server2",
            registry_source="srcA",
            risk_tier="Medium",
        ),
        McpServerRegistry(
            server_id="s3",
            name="Server3",
            registry_source="srcA",
            risk_tier="High",
        ),
        McpServerRegistry(
            server_id="s4",
            name="Server4",
            registry_source="srcB",
            risk_tier="Low",
        ),
        McpServerRegistry(
            server_id="s5",
            name="Server5",
            registry_source="srcB",
            risk_tier="Medium",
        ),
        McpServerRegistry(
            server_id="s6",
            name="Server6",
            registry_source="srcB",
            risk_tier="High",
        ),
    ]
    db = TestSession()
    db.add_all(seed)
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/api/registry/composition")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # overall total
    assert data["total_servers"] == 6, "Total server count mismatch"

    # two registry sources expected
    assert len(data["sources"]) == 2, "Expected 2 registry_source groups"

    # each source should have 3 servers, one per tier
    for src in data["sources"]:
        assert src["total"] == 3, f"Source {src['registry_source']} total mismatch"
        tier_counts = {t["risk_tier"]: t["count"] for t in src["tiers"]}
        assert tier_counts == {"Low": 1, "Medium": 1, "High": 1}, (
            f"Tier counts mismatch for source {src['registry_source']}"
        )

    print("PASS")


if __name__ == "__main__":
    _self_test()