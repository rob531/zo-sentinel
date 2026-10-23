# services/staged/server_freshness_ranking/logic.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry, Base  # Base for metadata creation

router = APIRouter(prefix="/api")


class ServerInfo(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str = Field(..., description="Human readable name")
    url: str = Field(..., description="Server URL")
    last_scanned: datetime | None = Field(
        None, description="Timestamp of the most recent scan"
    )
    scan_count: int = Field(..., description="Number of scans performed")
    freshness_score: float = Field(
        ..., description="Seconds since last scan (higher = less fresh)"
    )


class FreshnessRankingResponse(BaseModel):
    servers: List[ServerInfo]


def _compute_freshness_score(last_scanned: datetime | None) -> float:
    """Return seconds since last_scanned; if never scanned, return a very large value."""
    if last_scanned is None:
        return float("inf")
    return (datetime.utcnow() - last_scanned).total_seconds()


@router.get(
    "/risk/freshness/ranking",
    response_model=FreshnessRankingResponse,
    summary="Ranking of servers by freshness",
)
def get_freshness_ranking(session=Depends(get_session)):
    """Read the server registry and return a ranking based on freshness."""
    servers = session.query(McpServerRegistry).all()
    result = []
    for srv in servers:
        score = _compute_freshness_score(srv.last_scanned)
        result.append(
            ServerInfo(
                server_id=str(srv.server_id),
                name=srv.name,
                url=srv.url,
                last_scanned=srv.last_scanned,
                scan_count=srv.scan_count or 0,
                freshness_score=score,
            )
        )
    # Most fresh first (lowest score)
    result.sort(key=lambda x: x.freshness_score)
    return FreshnessRankingResponse(servers=result)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite DB mirroring the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed data
    now = datetime.utcnow()
    srv_a = McpServerRegistry(
        server_id="a1",
        name="Alpha",
        url="https://alpha.example.com",
        last_scanned=now,
        scan_count=10,
    )
    srv_b = McpServerRegistry(
        server_id="b2",
        name="Beta",
        url="https://beta.example.com",
        last_scanned=now.replace(hour=now.hour - 5),
        scan_count=5,
    )
    srv_c = McpServerRegistry(
        server_id="c3",
        name="Gamma",
        url="https://gamma.example.com",
        last_scanned=None,
        scan_count=0,
    )
    with SessionLocal() as db:
        db.add_all([srv_a, srv_b, srv_c])
        db.commit()

    # FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    resp = client.get("/api/risk/freshness/ranking")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    servers = data["servers"]
    # Expected order: Alpha (most recent), Beta (5h ago), Gamma (never scanned -> inf)
    expected_order = ["a1", "b2", "c3"]
    actual_order = [s["server_id"] for s in servers]
    assert actual_order == expected_order, f"Order mismatch {actual_order}"
    print("PASS")