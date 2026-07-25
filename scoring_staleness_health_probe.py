import datetime
from datetime import datetime as dt, timedelta

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Application imports (must be the real app models and session provider)
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class UnscoredServer(BaseModel):
    server_id: int
    name: str
    first_seen: dt
    url: str | None = None
    registry_source: str | None = None


class StaleServer(BaseModel):
    server_id: int
    name: str
    last_scored: dt | None = None
    age_hours: float | None = None
    url: str | None = None
    registry_source: str | None = None


class StalenessReport(BaseModel):
    total_servers: int
    unscored_count: int
    stale_count: int
    fresher_count: int
    unscored_servers: list[UnscoredServer] = Field(default_factory=list)
    stale_servers: list[StaleServer] = Field(default_factory=list)
    freshness_pct: float


# ----------------------------------------------------------------------
# FastAPI app and endpoint
# ----------------------------------------------------------------------
app = FastAPI()


@app.get(
    "/scoring/staleness-report",
    response_model=StalenessReport,
    summary="Report on scoring freshness for registered servers",
)
def scoring_staleness_report(
    min_age_hours: int = Query(24, ge=0, description="Staleness threshold in hours"),
    include_unscored: bool = Query(True, description="Whether to list unscored servers"),
    session: Session = Depends(get_session),
):
    """Generate a staleness report for servers in the registry."""
    now = dt.utcnow()
    threshold = now - timedelta(hours=min_age_hours)

    # Sub‑query: latest score per server
    latest_score_subq = (
        session.query(
            McpLlmAxisScores.server_id,
            func.max(McpLlmAxisScores.scored_at).label("last_scored"),
        )
        .group_by(McpLlmAxisScores.server_id)
        .subquery()
    )

    # Main query: left‑join registry with latest scores
    rows = (
        session.query(
            McpServerRegistry,
            latest_score_subq.c.last_scored,
        )
        .outerjoin(
            latest_score_subq,
            McpServerRegistry.server_id == latest_score_subq.c.server_id,
        )
        .all()
    )

    total_servers = len(rows)
    unscored_count = 0
    stale_count = 0
    fresher_count = 0
    unscored_servers: list[UnscoredServer] = []
    stale_servers: list[StaleServer] = []

    for server, last_scored in rows:
        if last_scored is None:
            unscored_count += 1
            if include_unscored:
                unscored_servers.append(
                    UnscoredServer(
                        server_id=server.server_id,
                        name=server.name,
                        first_seen=server.first_seen,
                        url=getattr(server, "url", None),
                        registry_source=getattr(server, "registry_source", None),
                    )
                )
        else:
            age = now - last_scored
            if age > threshold:
                stale_count += 1
                stale_servers.append(
                    StaleServer(
                        server_id=server.server_id,
                        name=server.name,
                        last_scored=last_scored,
                        age_hours=age.total_seconds() / 3600,
                        url=getattr(server, "url", None),
                        registry_source=getattr(server, "registry_source", None),
                    )
                )
            else:
                fresher_count += 1

    freshness_pct = (
        (fresher_count / total_servers) * 100 if total_servers > 0 else 0.0
    )

    return StalenessReport(
        total_servers=total_servers,
        unscored_count=unscored_count,
        stale_count=stale_count,
        fresher_count=fresher_count,
        unscored_servers=unscored_servers,
        stale_servers=stale_servers,
        freshness_pct=round(freshness_pct, 2),
    )


# ----------------------------------------------------------------------
# Self‑test when run as a script
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB with the same metadata
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.models import Base  # declarative_base used by the real app

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = dt.utcnow()
    sess = TestSession()
    # Servers
    servers = [
        McpServerRegistry(
            server_id=1,
            name="srv-unscored-1",
            url="http://example.com/1",
            registry_source="src",
            first_seen=now - timedelta(days=3),
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id=2,
            name="srv-unscored-2",
            url="http://example.com/2",
            registry_source="src",
            first_seen=now - timedelta(days=2),
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id=3,
            name="srv-stale-1",
            url="http://example.com/3",
            registry_source="src",
            first_seen=now - timedelta(days=5),
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id=4,
            name="srv-stale-2",
            url="http://example.com/4",
            registry_source="src",
            first_seen=now - timedelta(days=4),
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id=5,
            name="srv-fresh",
            url="http://example.com/5",
            registry_source="src",
            first_seen=now - timedelta(days=1),
            last_scanned=None,
            scan_count=0,
        ),
    ]
    sess.add_all(servers)
    sess.flush()

    # Scores
    scores = [
        McpLlmAxisScores(
            server_id=3,
            scored_at=now - timedelta(hours=30),  # stale (>24h)
        ),
        McpLlmAxisScores(
            server_id=4,
            scored_at=now - timedelta(hours=48),  # stale (>24h)
        ),
        McpLlmAxisScores(
            server_id=5,
            scored_at=now - timedelta(hours=5),  # fresh
        ),
    ]
    sess.add_all(scores)
    sess.commit()

    client = TestClient(app)

    resp = client.get("/scoring/staleness-report")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 5
    assert data["unscored_count"] == 2
    assert data["stale_count"] == 2
    assert data["fresher_count"] == 1
    assert len(data["unscored_servers"]) == 2
    assert len(data["stale_servers"]) == 2
    # freshness_pct should be 20.0 (1/5*100)
    assert abs(data["freshness_pct"] - 20.0) < 0.01

    print("PASS")