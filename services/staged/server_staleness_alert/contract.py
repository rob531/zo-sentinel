import datetime
from datetime import datetime as dt, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class ServerStaleInfo(BaseModel):
    server_id: str
    name: str
    registry_source: str
    url: str
    risk_tier: str
    last_scored_at: Optional[datetime.datetime] = None
    stale_hours: float = Field(..., description="Hours since last score (or since epoch if never scored)")


class ServerStalenessResponse(BaseModel):
    servers: List[ServerStaleInfo]


def _latest_score_subquery():
    """Sub‑query returning the most recent score timestamp per server."""
    return (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )


def get_stale_servers(
    hours: int,
    limit: int,
    session: Session,
) -> List[dict]:
    """Return servers whose latest axis score is older than *hours* or missing."""
    cutoff = dt.utcnow() - timedelta(hours=hours)

    latest_scores = _latest_score_subquery()

    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
            McpServerRegistry.risk_tier,
            latest_scores.c.last_scored_at,
        )
        .select_from(McpServerRegistry)
        .outerjoin(latest_scores, McpServerRegistry.server_id == latest_scores.c.server_id)
        .where(
            (latest_scores.c.last_scored_at.is_(None))
            | (latest_scores.c.last_scored_at < cutoff)
        )
    )

    rows = session.execute(stmt).all()

    # Compute stale_hours in Python for portability across DB back‑ends
    results = []
    for (
        server_id,
        name,
        registry_source,
        url,
        risk_tier,
        last_scored_at,
    ) in rows:
        if last_scored_at is None:
            stale_hours = float("inf")
        else:
            stale_hours = (dt.utcnow() - last_scored_at).total_seconds() / 3600.0
        results.append(
            {
                "server_id": server_id,
                "name": name,
                "registry_source": registry_source,
                "url": url,
                "risk_tier": risk_tier,
                "last_scored_at": last_scored_at,
                "stale_hours": stale_hours,
            }
        )

    # Order by stale_hours descending (inf first) and apply limit
    results.sort(key=lambda x: x["stale_hours"], reverse=True)
    return results[:limit]


@router.get(
    "/servers/staleness",
    response_model=ServerStalenessResponse,
    name="server_staleness_alert:get_stale_servers",
)
def server_staleness_endpoint(
    hours: int = Query(72, ge=1),
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
):
    servers = get_stale_servers(hours=hours, limit=limit, session=session)
    # Convert infinite stale_hours (no scores) to a large number for JSON serialisation
    for s in servers:
        if s["stale_hours"] == float("inf"):
            s["stale_hours"] = (dt.utcnow() - dt(1970, 1, 1)).total_seconds() / 3600.0
    return ServerStalenessResponse(servers=servers)


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.server_staleness_alert.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine (static pool to allow multiple connections)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data
    now = dt.utcnow()
    sess = TestingSessionLocal()
    try:
        # Four servers
        servers = [
            McpServerRegistry(
                server_id="srv_fresh_1",
                name="Fresh Server 1",
                registry_source="src_a",
                url="https://example.com/1",
                risk_tier="low",
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="srv_fresh_2",
                name="Fresh Server 2",
                registry_source="src_b",
                url="https://example.com/2",
                risk_tier="medium",
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="srv_stale_1",
                name="Stale Server 1",
                registry_source="src_c",
                url="https://example.com/3",
                risk_tier="high",
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="srv_stale_2",
                name="Stale Server 2",
                registry_source="src_d",
                url="https://example.com/4",
                risk_tier="critical",
                last_seen=now,
            ),
        ]
        sess.add_all(servers)

        # Fresh scores (within 10h)
        fresh_scores = [
            McpLlmAxisScore(
                server_id="srv_fresh_1",
                scored_at=now - timedelta(hours=10),
                axis_name="reliability",
                adapter_sha256="a" * 64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=1,
                label="ok",
                label_index=0,
                model_version="m1",
                p_critical=0.0,
                p_danger=0.0,
                p_top=1.0,
                probs="{}",
            ),
            McpLlmAxisScore(
                server_id="srv_fresh_2",
                scored_at=now - timedelta(hours=5),
                axis_name="reliability",
                adapter_sha256="b" * 64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=2,
                label="ok",
                label_index=0,
                model_version="m1",
                p_critical=0.0,
                p_danger=0.0,
                p_top=1.0,
                probs="{}",
            ),
        ]
        # Stale scores (96h ago)
        stale_scores = [
            McpLlmAxisScore(
                server_id="srv_stale_1",
                scored_at=now - timedelta(hours=96),
                axis_name="reliability",
                adapter_sha256="c" * 64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=3,
                label="ok",
                label_index=0,
                model_version="m1",
                p_critical=0.0,
                p_danger=0.0,
                p_top=1.0,
                probs="{}",
            ),
            McpLlmAxisScore(
                server_id="srv_stale_2",
                scored_at=now - timedelta(hours=96),
                axis_name="reliability",
                adapter_sha256="d" * 64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=4,
                label="ok",
                label_index=0,
                model_version="m1",
                p_critical=0.0,
                p_danger=0.0,
                p_top=1.0,
                probs="{}",
            ),
        ]
        sess.add_all(fresh_scores + stale_scores)
        sess.commit()
    finally:
        sess.close()

    # Build FastAPI app with overridden dependency
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    response = client.get("/api/servers/staleness?hours=72&limit=50")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert isinstance(data, dict), "Response is not a dict"
    assert "servers" in data, "'servers' key missing"
    servers = data["servers"]
    assert len(servers) == 2, f"Expected 2 stale servers, got {len(servers)}"
    # Ensure ordering by stale_hours descending
    assert servers[0]["stale_hours"] >= servers[1]["stale_hours"], "Servers not ordered by stale_hours desc"

    print("PASS")