from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_source_tier_matrix
except ImportError:
    get_source_tier_matrix = None


router = APIRouter(prefix="/api", tags=["registry"])


class RegistrySourceTierRow(BaseModel):
    registry_source: str
    server_count: int = Field(ge=0)
    mean_trust_score: float | None = None
    stale_count: int = Field(ge=0)
    unscanned_count: int = Field(ge=0)
    tiers: dict[str, int] = Field(default_factory=dict)


class RegistrySourceTierMatrixResponse(BaseModel):
    sources: list[RegistrySourceTierRow]


def _build_source_tier_matrix(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_seen,
            McpServerRegistry.scan_count,
        )
    ).all()

    tier_names = sorted(
        {
            str(row.risk_tier)
            for row in rows
            if row.risk_tier is not None
        }
    )
    grouped: dict[str, list[Any]] = {}

    for row in rows:
        source = str(row.registry_source)
        grouped.setdefault(source, []).append(row)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    sources: list[dict[str, Any]] = []

    for source in sorted(grouped):
        source_rows = grouped[source]
        trust_scores = [
            float(row.trust_score)
            for row in source_rows
            if row.trust_score is not None
        ]
        stale_count = 0
        for row in source_rows:
            last_seen = row.last_seen
            if last_seen is None:
                continue
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if last_seen < cutoff:
                stale_count += 1

        tier_counts = {tier: 0 for tier in tier_names}
        for row in source_rows:
            if row.risk_tier is not None:
                tier_counts[str(row.risk_tier)] += 1

        sources.append(
            {
                "registry_source": source,
                "server_count": len(source_rows),
                "mean_trust_score": (
                    sum(trust_scores) / len(trust_scores)
                    if trust_scores
                    else None
                ),
                "stale_count": stale_count,
                "unscanned_count": sum(
                    1
                    for row in source_rows
                    if row.scan_count is None or row.scan_count == 0
                ),
                "tiers": tier_counts,
            }
        )

    return {"sources": sources}


@router.get(
    "/registry/source-tier-matrix",
    response_model=RegistrySourceTierMatrixResponse,
)
def registry_source_tier_matrix(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if get_source_tier_matrix is not None:
        return get_source_tier_matrix(session)
    return _build_source_tier_matrix(session)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.execute(
            insert(McpServerRegistry),
            [
                {
                    "server_id": "self-test-1",
                    "registry_source": "github",
                    "risk_tier": "low",
                    "trust_score": 0.9,
                    "last_seen": now - timedelta(days=31),
                    "scan_count": 0,
                },
                {
                    "server_id": "self-test-2",
                    "registry_source": "github",
                    "risk_tier": "high",
                    "trust_score": 0.7,
                    "last_seen": now,
                    "scan_count": 2,
                },
                {
                    "server_id": "self-test-3",
                    "registry_source": "gitlab",
                    "risk_tier": "low",
                    "trust_score": 0.5,
                    "last_seen": now - timedelta(days=31),
                    "scan_count": None,
                },
            ],
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/source-tier-matrix")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sources"]) == 2
        assert all(
            sum(source["tiers"].values()) == source["server_count"]
            for source in payload["sources"]
        )

    print("PASS")


if __name__ == "__main__":
    _self_test()