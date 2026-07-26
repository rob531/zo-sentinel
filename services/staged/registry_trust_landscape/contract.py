import datetime
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, distinct, select
from sqlalchemy.orm import Session

# Real data layer imports (must remain unchanged)
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Base  # type: ignore

router = APIRouter(prefix="/api")


class SourceInfo(BaseModel):
    registry_source: str
    server_count: int
    avg_risk_score: float
    tier_distribution: Dict[str, int]
    signal_coverage_pct: float


class TrustLandscapeResponse(BaseModel):
    generated_at: datetime.datetime
    sources: List[SourceInfo]


@router.get(
    "/registry/trust-landscape",
    response_model=TrustLandscapeResponse,
    tags=["registry"],
)
def get_trust_landscape(session: Session = Depends(get_session)):
    # ----- total servers per source -----
    total_q = (
        session.query(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("server_count"),
        )
        .group_by(McpServerRegistry.registry_source)
        .subquery()
    )

    # ----- average overall_risk per source -----
    avg_q = (
        session.query(
            McpServerRegistry.registry_source,
            func.avg(McpLlmAxisScore.overall_risk).label("avg_risk_score"),
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .group_by(McpServerRegistry.registry_source)
        .subquery()
    )

    # ----- tier distribution per source -----
    tier_q = (
        session.query(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
        .subquery()
    )

    # ----- servers with >=5 distinct axes scored -----
    axis_cnt_q = (
        session.query(
            McpLlmAxisScore.server_id,
            func.count(distinct(McpLlmAxisScore.axis_name)).label("axis_cnt"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    covered_q = (
        session.query(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("covered"),
        )
        .join(
            axis_cnt_q,
            McpServerRegistry.server_id == axis_cnt_q.c.server_id,
        )
        .filter(axis_cnt_q.c.axis_cnt >= 5)
        .group_by(McpServerRegistry.registry_source)
        .subquery()
    )

    # ----- assemble results -----
    sources: List[SourceInfo] = []
    total_rows = session.query(total_q).all()
    for row in total_rows:
        src = row.registry_source
        server_count = row.server_count

        avg_row = session.query(avg_q).filter(avg_q.c.registry_source == src).first()
        avg_risk_score = float(avg_row.avg_risk_score) if avg_row and avg_row.avg_risk_score else 0.0

        # tier distribution
        tier_rows = (
            session.query(tier_q)
            .filter(tier_q.c.registry_source == src)
            .all()
        )
        tier_dist: Dict[str, int] = {}
        for tr in tier_rows:
            tier_dist[tr.risk_tier] = tr.cnt

        # signal coverage
        covered_row = (
            session.query(covered_q)
            .filter(covered_q.c.registry_source == src)
            .first()
        )
        covered = covered_row.covered if covered_row else 0
        signal_coverage_pct = (covered / server_count) * 100 if server_count else 0.0

        sources.append(
            SourceInfo(
                registry_source=src,
                server_count=server_count,
                avg_risk_score=avg_risk_score,
                tier_distribution=tier_dist,
                signal_coverage_pct=signal_coverage_pct,
            )
        )

    return TrustLandscapeResponse(
        generated_at=datetime.datetime.utcnow(),
        sources=sources,
    )


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_trust_landscape.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for the acceptance test
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed data
    sess: Session = SessionLocal()
    # Registry sources
    src_a = "source_a"
    src_b = "source_b"

    # Servers (6 total)
    servers = [
        McpServerRegistry(
            server_id=1,
            registry_source=src_a,
            risk_tier="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id=2,
            registry_source=src_a,
            risk_tier="TRUSTED_RESEARCH",
        ),
        McpServerRegistry(
            server_id=3,
            registry_source=src_a,
            risk_tier="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id=4,
            registry_source=src_b,
            risk_tier="TRUSTED_RESEARCH",
        ),
        McpServerRegistry(
            server_id=5,
            registry_source=src_b,
            risk_tier="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id=6,
            registry_source=src_b,
            risk_tier="TRUSTED_RESEARCH",
        ),
    ]
    sess.add_all(servers)

    # Axis scores (mix of >=5 axes for some servers)
    axis_names = ["axis1", "axis2", "axis3", "axis4", "axis5", "axis6"]
    scores = []
    for srv in servers:
        # give each server 4 or 6 axes scored
        n_axes = 6 if srv.server_id % 2 == 0 else 4
        for i in range(n_axes):
            scores.append(
                McpLlmAxisScore(
                    server_id=srv.server_id,
                    axis_name=axis_names[i],
                    overall_risk=0.5 * (srv.server_id + i),
                )
            )
    sess.add_all(scores)
    sess.commit()
    sess.close()

    # Override dependency
    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/registry/trust-landscape")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "sources" in data, "Missing sources key"
    assert len(data["sources"]) == 2, f"Expected 2 sources, got {len(data['sources'])}"
    for src in data["sources"]:
        assert isinstance(src["avg_risk_score"], float), "avg_risk_score not float"
        assert isinstance(src["tier_distribution"], dict), "tier_distribution not dict"
        # ensure at least one known tier appears
        assert any(
            tier in src["tier_distribution"] for tier in ("TRUSTED_GENERAL", "TRUSTED_RESEARCH")
        ), "Expected tier keys missing"

    print("PASS")