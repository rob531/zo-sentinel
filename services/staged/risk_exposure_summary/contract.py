"""services/staged/risk_exposure_summary/contract.py

FastAPI contract for the risk exposure summary service.
Mirrors the structure of services/_exemplar/contract.py.
"""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class ServerInfo(BaseModel):
    server_id: str
    name: str
    risk_tier: str


class TierInfo(BaseModel):
    tier: str
    count: int
    percentage: float
    servers: List[ServerInfo]


class ExposureSummary(BaseModel):
    total: int
    tiers: List[TierInfo]


@router.get(
    "/risk/exposure-summary",
    response_model=ExposureSummary,
    tags=["risk exposure summary"],
)
def get_exposure_summary(session: Session = Depends(get_session)):
    servers = session.query(McpServerRegistry).all()
    total = len(servers)

    tier_map = {}
    for srv in servers:
        tier = srv.risk_tier or "UNKNOWN"
        tier_entry = tier_map.setdefault(tier, {"count": 0, "servers": []})
        tier_entry["count"] += 1
        tier_entry["servers"].append(
            ServerInfo(
                server_id=str(srv.server_id),
                name=srv.name or "",
                risk_tier=tier,
            )
        )

    tiers: List[TierInfo] = []
    for tier, data in tier_map.items():
        count = data["count"]
        percentage = round((count / total) * 100, 2) if total else 0.0
        tiers.append(
            TierInfo(
                tier=tier,
                count=count,
                percentage=percentage,
                servers=data["servers"],
            )
        )

    return ExposureSummary(total=total, tiers=tiers)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.risk_exposure_summary.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    # Build a minimal FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # --------------------------------------------------------------------- #
    SQLITE_URL = "sqlite://"
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # --------------------------------------------------------------------- #
    # Seed test data (6 servers, mixed risk tiers)
    # --------------------------------------------------------------------- #
    test_servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="alpha",
            risk_tier="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="bravo",
            risk_tier="TRUSTED_RESEARCH",
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="charlie",
            risk_tier="ENTERPRISE_CONTROLLED",
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="delta",
            risk_tier="HIGH_RISK_ISOLATED",
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="echo",
            risk_tier="KNOWN_THREAT",
        ),
        McpServerRegistry(
            server_id="srv-6",
            name="foxtrot",
            risk_tier="TRUSTED_GENERAL",
        ),
    ]

    with SessionLocal() as db:
        db.add_all(test_servers)
        db.commit()

    # --------------------------------------------------------------------- #
    # Execute test request
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/risk/exposure-summary")
    try:
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert data["total"] == 6, f"Total mismatch: {data['total']}"
        distinct_tiers = {t["tier"] for t in data["tiers"]}
        assert len(distinct_tiers) >= 3, f"Not enough distinct tiers: {distinct_tiers}"
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)