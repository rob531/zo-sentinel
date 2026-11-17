"""HTTP surface for organization watchlist digests."""
from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, Org

from .logic import get_watchlist_digest

router = APIRouter(prefix="/api", tags=["org_watchlist_digest"])


class TopRiskyServerResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    trust_score: float


class WatchlistDigestResponse(BaseModel):
    org_id: int
    org_name: str
    watched: int
    by_tier: dict[str, int]
    open_disputes: int
    top_risky: list[TopRiskyServerResponse]


@router.get(
    "/orgs/{org_id}/watchlist-digest",
    response_model=WatchlistDigestResponse,
)
def get_org_watchlist_digest(
    org_id: int,
    db: Session = Depends(get_session),
) -> WatchlistDigestResponse:
    return WatchlistDigestResponse(**get_watchlist_digest(db, org_id))


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    Org.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)

    with SessionLocal() as session:
        session.add(Org(id=1, name="Acme"))
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-high",
                    name="High Risk Server",
                    risk_tier="high",
                    trust_score=25.0,
                ),
                McpServerRegistry(
                    server_id="server-medium",
                    name="Medium Risk Server",
                    risk_tier="medium",
                    trust_score=55.0,
                ),
                McpServerRegistry(
                    server_id="server-medium-2",
                    name="Another Medium Server",
                    risk_tier="medium",
                    trust_score=65.0,
                ),
            ]
        )
        session.add(
            McpScoreDispute(
                server_id="server-high",
                explanation="Trust score requires review.",
                proposed_overall_risk="high",
                reason_category="trust_score",
                status="open",
                submitted_by="security@example.com",
            )
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/1/watchlist-digest")
        assert response.status_code == 200
        payload = response.json()
        assert payload["org_id"] == 1
        assert payload["org_name"] == "Acme"
        assert payload["watched"] == 3
        assert sum(payload["by_tier"].values()) == 3
        assert payload["open_disputes"] == 1
        assert payload["top_risky"]

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()