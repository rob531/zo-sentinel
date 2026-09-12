"""services/staged/registry_tier_distribution/contract.py

FastAPI contract for the ``registry_tier_distribution`` staged service.

Provides:
    GET /api/registry/tier-distribution
        Returns a mapping of risk tier → number of servers in that tier.

The module can be executed directly to run a self‑test that seeds an
in‑memory SQLite database, invokes the endpoint via ``TestClient`` and
verifies the response.
"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

# --------------------------------------------------------------------------- #
# Real data‑layer imports – must be used in production code.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import McpServerRegistry, Base  # Base is the declarative base.

# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/registry/tier-distribution",
    response_model=Dict[str, int],
    summary="Distribution of servers per risk tier",
)
def get_distribution(session: Session = Depends(get_session)) -> Dict[str, int]:
    """
    Count servers grouped by ``risk_tier`` in the ``mcp_server_registry`` table.
    """
    stmt = (
        select(McpServerRegistry.risk_tier, func.count().label("cnt"))
        .group_by(McpServerRegistry.risk_tier)
    )
    rows = session.execute(stmt).all()
    return {tier: count for tier, count in rows}


# --------------------------------------------------------------------------- #
# Self‑test (executed with ``python -m services.staged.registry_tier_distribution.contract``)
# --------------------------------------------------------------------------- #
def _get_test_session() -> Session:
    """
    Create a new SQLAlchemy Session bound to an in‑memory SQLite database.
    The database schema is created from the real ``Base`` metadata.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    return TestSession()


def _seed_test_data(session: Session) -> None:
    """Insert three servers for each of three risk tiers."""
    tiers = ["low", "medium", "high"]
    for tier in tiers:
        for i in range(3):
            server = McpServerRegistry(
                server_id=f"{tier}_{i}",
                name=f"{tier} server {i}",
                risk_tier=tier,
            )
            session.add(server)
    session.commit()


def _run_self_test() -> None:
    app = FastAPI()
    app.include_router(router)

    # Override the real ``get_session`` dependency with the in‑memory one.
    test_session = _get_test_session()
    _seed_test_data(test_session)

    def override_get_session() -> Session:
        return test_session

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/registry/tier-distribution")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    expected = {"low": 3, "medium": 3, "high": 3}
    assert data == expected, f"Expected {expected}, got {data}"
    print("PASS")


if __name__ == "__main__":
    _run_self_test()