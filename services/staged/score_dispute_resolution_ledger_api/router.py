"""HTTP surface for the score dispute resolution ledger."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

from .logic import fetch_dispute_ledger

router = APIRouter(prefix="/api/disputes", tags=["disputes"])

TierDelta = Literal["up", "down", "same", "unknown"]


class DisputeLedgerItem(BaseModel):
    id: int
    server_id: str
    name: str | None
    current_risk_tier: str | None
    submitted_by: str
    proposed_overall_risk: str
    reason_category: str
    status: str
    explanation: str
    admin_note: str | None
    created_at: str
    resolved_at: str | None
    tier_delta: TierDelta


class DisputeLedgerResponse(BaseModel):
    total: int
    items: list[DisputeLedgerItem]


@router.get("/ledger", response_model=DisputeLedgerResponse)
def get_dispute_ledger(
    db: Session = Depends(get_session),
    status: str | None = Query(default=None),
    server_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
) -> DisputeLedgerResponse:
    return DisputeLedgerResponse(
        **fetch_dispute_ledger(
            db,
            status=status,
            server_id=server_id,
            limit=limit,
            offset=offset,
        )
    )


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add(
            McpServerRegistry(
                server_id="server-1",
                name="Registered Server",
                risk_tier="TRUSTED_GENERAL",
            )
        )
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                McpScoreDispute(
                    server_id="server-1",
                    submitted_by="analyst-1",
                    proposed_overall_risk="TRUSTED_GENERAL",
                    reason_category="correction",
                    explanation="Open dispute",
                    status="OPEN",
                    created_at=now,
                ),
                McpScoreDispute(
                    server_id="server-1",
                    submitted_by="analyst-2",
                    proposed_overall_risk="TRUSTED_GENERAL",
                    reason_category="correction",
                    explanation="Resolved dispute",
                    status="RESOLVED",
                    admin_note="No change required",
                    created_at=now,
                    resolved_at=now,
                ),
                McpScoreDispute(
                    server_id="server-without-registry",
                    submitted_by="analyst-3",
                    proposed_overall_risk="TRUSTED_GENERAL",
                    reason_category="correction",
                    explanation="Missing registry entry",
                    status="PENDING",
                    created_at=now,
                ),
            ]
        )
        session.commit()

        def override_get_session():
            yield session

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/disputes/ledger")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 3
            assert len(payload["items"]) == 3
            assert sum(
                item["tier_delta"] == "unknown" for item in payload["items"]
            ) == 1

            open_response = client.get(
                "/api/disputes/ledger",
                params={"status": "OPEN"},
            )
            assert open_response.status_code == 200
            open_payload = open_response.json()
            assert open_payload["total"] == 1
            assert len(open_payload["items"]) == 1
            assert open_payload["items"][0]["status"] == "OPEN"

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()