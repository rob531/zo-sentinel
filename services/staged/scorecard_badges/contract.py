"""services/staged/scorecard_badges/contract.py

FastAPI contract for the ``scorecard_badges`` service.

Provides:
    GET /api/scorecard/badge?server_id={server_id}
        → {"server_id": "...", "badge_tier": "...", "badge_url": "..."}
"""

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

# Real data layer imports – must not be re‑implemented.
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class BadgeResponse(BaseModel):
    server_id: str
    badge_tier: str
    badge_url: HttpUrl


# Simple mapping from a server's ``risk_tier`` to a badge tier.
_RISK_TO_BADGE = {
    "none": "bronze",
    "low": "bronze",
    "medium": "silver",
    "high": "gold",
    "critical": "gold",
}


def _determine_badge(risk_tier: str | None) -> str:
    """Return the badge tier for a given risk tier."""
    if not risk_tier:
        return "bronze"
    return _RISK_TO_BADGE.get(risk_tier.lower(), "bronze")


@router.get(
    "/scorecard/badge",
    response_model=BadgeResponse,
    summary="Return the badge for a server",
)
def get_badge(
    server_id: str = Query(..., description="The server identifier"),
    session: Session = Depends(get_session),
):
    """Fetch a server and compute its badge."""
    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    badge_tier = _determine_badge(server.risk_tier)
    badge_url = f"https://example.com/badges/{badge_tier}.png"

    return BadgeResponse(
        server_id=server.server_id,
        badge_tier=badge_tier,
        badge_url=badge_url,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.scorecard_badges.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the models we actually use.
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    # ------------------------------------------------------------------- #
    # Seed three servers with distinct risk tiers.
    # ------------------------------------------------------------------- #
    seed_data = [
        {
            "server_id": "srv-001",
            "name": "Alpha",
            "risk_tier": "low",
        },
        {
            "server_id": "srv-002",
            "name": "Beta",
            "risk_tier": "medium",
        },
        {
            "server_id": "srv-003",
            "name": "Gamma",
            "risk_tier": "high",
        },
    ]

    with SessionLocal() as db:
        for rec in seed_data:
            db.add(McpServerRegistry(**rec))
        db.commit()

    # ------------------------------------------------------------------- #
    # Build a FastAPI app and override the session dependency.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def _override_get_session():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Run assertions.
    # ------------------------------------------------------------------- #
    expected = {
        "srv-001": "bronze",
        "srv-002": "silver",
        "srv-003": "gold",
    }

    for sid, tier in expected.items():
        resp = client.get(f"/api/scorecard/badge?server_id={sid}")
        assert resp.status_code == 200, f"{sid} returned {resp.status_code}"
        data = resp.json()
        assert data["server_id"] == sid
        assert data["badge_tier"] == tier
        assert data["badge_url"].endswith(f"{tier}.png")

    print("PASS")
    sys.exit(0)