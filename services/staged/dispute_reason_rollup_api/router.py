from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_reason_rollup
except ImportError:  # pragma: no cover - supports direct execution of this file
    from logic import get_reason_rollup  # type: ignore


class ReasonCategoryRollup(BaseModel):
    reason_category: str
    total: int
    open: int
    resolved: int
    rejected: int
    median_open_age_days: float


class ReasonRollupResponse(BaseModel):
    total: int
    categories: list[ReasonCategoryRollup]


router = APIRouter(prefix="/api")


@router.get(
    "/disputes/reason-rollup",
    response_model=ReasonRollupResponse,
    summary="Roll up score disputes by reason category",
)
def reason_rollup(
    session: Session = Depends(get_session),
    status: Optional[Literal["open", "resolved", "rejected"]] = Query(
        default=None,
        description="Optionally restrict the rollup to one dispute status.",
    ),
) -> ReasonRollupResponse:
    result = get_reason_rollup(session, status=status)

    if isinstance(result, dict):
        payload = dict(result)
    elif hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif hasattr(result, "dict"):
        payload = result.dict()
    else:
        payload = {
            "total": getattr(result, "total", 0),
            "categories": getattr(result, "categories", []),
        }

    categories = list(payload.get("categories") or [])
    categories.sort(key=lambda category: int(category["total"]), reverse=True)
    payload["categories"] = categories[:50]

    return ReasonRollupResponse.parse_obj(payload)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        ("alpha", "open", now - timedelta(days=2)),
        ("alpha", "open", now - timedelta(days=4)),
        ("alpha", "resolved", now - timedelta(days=8)),
        ("beta", "resolved", now - timedelta(days=3)),
        ("beta", "rejected", now - timedelta(days=5)),
        ("gamma", "open", now - timedelta(days=1)),
    ]

    with Session(engine) as session:
        for category, dispute_status, created_at in rows:
            session.add(
                McpScoreDispute(
                    server_id="server-test",
                    submitted_by="test-user",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    reason_category=category,
                    explanation="Self-test dispute",
                    status=dispute_status,
                    created_at=created_at,
                    resolved_at=(
                        created_at + timedelta(days=1)
                        if dispute_status != "open"
                        else None
                    ),
                )
            )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get("/api/disputes/reason-rollup")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 6
    assert len(body["categories"]) == 3
    assert body["categories"][0]["reason_category"] == "alpha"
    assert body["categories"][0]["total"] == 3
    assert body["categories"][0]["open"] == 2
    assert body["categories"][0]["resolved"] == 1
    assert body["categories"][0]["rejected"] == 0
    assert body["categories"][1]["total"] == 2
    assert body["categories"][2]["total"] == 1
    assert all(
        body["categories"][index]["total"] >= body["categories"][index + 1]["total"]
        for index in range(len(body["categories"]) - 1)
    )

    filtered = client.get("/api/disputes/reason-rollup?status=open")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 3

    print("PASS")


if __name__ == "__main__":
    _self_test()