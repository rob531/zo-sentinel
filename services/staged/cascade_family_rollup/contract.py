import datetime
from collections import defaultdict
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


def _extract_family(url: str) -> str:
    """Return the top‑level domain (e.g. ``github.com``) from a URL."""
    try:
        netloc = urlparse(url).netloc
        parts = netloc.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return ""


class FamilySeriesItem(BaseModel):
    family: str
    server_count: int
    avg_trust_score: float | None
    risk_tiers: dict[str, int]
    first_seen: datetime.datetime | None
    last_seen: datetime.datetime | None


class FamilyRollupResponse(BaseModel):
    total_families: int
    total_servers: int
    series: list[FamilySeriesItem]


@router.get(
    "/registry/family-rollup",
    response_model=FamilyRollupResponse,
    name="family_rollup",
)
def family_rollup(
    min_servers: int = Query(1, ge=1),
    db: Session = Depends(get_session),
):
    # Pull all servers with needed columns
    servers = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.url,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
        )
        .all()
    )

    families: dict[str, list[McpServerRegistry]] = defaultdict(list)
    for row in servers:
        family = _extract_family(row.url or "")
        families[family].append(row)

    series: list[FamilySeriesItem] = []
    total_servers = 0

    for family, rows in families.items():
        if len(rows) < min_servers:
            continue
        total_servers += len(rows)

        trust_scores = [r.trust_score for r in rows if r.trust_score is not None]
        avg_trust = sum(trust_scores) / len(trust_scores) if trust_scores else None

        risk_dist: dict[str, int] = defaultdict(int)
        for r in rows:
            tier = r.risk_tier or "unknown"
            risk_dist[tier] += 1

        first_seen_vals = [r.first_seen for r in rows if r.first_seen]
        last_seen_vals = [r.last_seen for r in rows if r.last_seen]

        series.append(
            FamilySeriesItem(
                family=family,
                server_count=len(rows),
                avg_trust_score=avg_trust,
                risk_tiers=dict(risk_dist),
                first_seen=min(first_seen_vals) if first_seen_vals else None,
                last_seen=max(last_seen_vals) if last_seen_vals else None,
            )
        )

    response = FamilyRollupResponse(
        total_families=len(series),
        total_servers=total_servers,
        series=sorted(series, key=lambda x: x.family),
    )
    return response


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    # Build a test FastAPI app with an in‑memory SQLite DB
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine)

    Base.metadata.create_all(bind=test_engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data: 4 servers across 2 families
    now = datetime.datetime.utcnow()
    seed = [
        McpServerRegistry(
            server_id="srv1",
            url="https://github.com/repo1",
            trust_score=0.8,
            risk_tier="low",
            first_seen=now - datetime.timedelta(days=10),
            last_seen=now - datetime.timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="srv2",
            url="https://github.com/repo2",
            trust_score=0.6,
            risk_tier="medium",
            first_seen=now - datetime.timedelta(days=9),
            last_seen=now - datetime.timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="srv3",
            url="https://api.example.com/endpoint1",
            trust_score=0.9,
            risk_tier="low",
            first_seen=now - datetime.timedelta(days=8),
            last_seen=now - datetime.timedelta(days=3),
        ),
        McpServerRegistry(
            server_id="srv4",
            url="https://api.example.com/endpoint2",
            trust_score=0.7,
            risk_tier="high",
            first_seen=now - datetime.timedelta(days=7),
            last_seen=now - datetime.timedelta(days=4),
        ),
    ]

    with TestSessionLocal() as sess:
        sess.add_all(seed)
        sess.commit()

    # Run test client
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/registry/family-rollup?min_servers=1")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["total_families"] == 2, "Expected 2 families"
    assert data["total_servers"] == 4, "Expected 4 servers total"
    assert len(data["series"]) == 2, "Series length mismatch"
    server_sum = sum(item["server_count"] for item in data["series"])
    assert server_sum == 4, "Server count sum mismatch"

    print("PASS")
    sys.exit(0)