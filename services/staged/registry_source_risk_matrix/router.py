from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_registry_source_risk_matrix as _get_registry_source_risk_matrix


class RegistrySourceRiskMatrixItem(BaseModel):
    registry_source: str
    total: int = Field(ge=0)
    mean_trust_score: float
    mean_scan_count: float
    stale_count: int = Field(ge=0)
    tiers: dict[str, int]


class RegistrySourceRiskMatrixResponse(BaseModel):
    days: int
    generated_at: datetime
    sources: list[RegistrySourceRiskMatrixItem]


router = APIRouter(prefix="/api", tags=["registry-source-risk-matrix"])


@router.get(
    "/registry/source-risk-matrix",
    response_model=RegistrySourceRiskMatrixResponse,
)
async def get_registry_source_risk_matrix(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> Any:
    result = _get_registry_source_risk_matrix(session, days)
    if inspect.isawaitable(result):
        result = await result
    return result


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-alpha-high",
                    registry_source="alpha",
                    risk_tier="HIGH",
                    trust_score=0.90,
                    scan_count=3,
                    last_seen=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="self-test-alpha-medium",
                    registry_source="alpha",
                    risk_tier="MEDIUM",
                    trust_score=0.70,
                    scan_count=1,
                    last_seen=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="self-test-beta-low",
                    registry_source="beta",
                    risk_tier="LOW",
                    trust_score=0.50,
                    scan_count=2,
                    last_seen=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="self-test-beta-medium",
                    registry_source="beta",
                    risk_tier="MEDIUM",
                    trust_score=0.60,
                    scan_count=4,
                    last_seen=datetime.utcnow(),
                ),
            ]
        )
        session.commit()

        def override_get_session():
            yield session

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = override_get_session
        test_app.include_router(router)

        response = TestClient(test_app).get(
            "/api/registry/source-risk-matrix",
            params={"days": 30},
        )
        response.raise_for_status()
        payload = response.json()

        assert payload["days"] == 30
        assert len(payload["sources"]) == 2

        sources = {item["registry_source"]: item for item in payload["sources"]}
        assert set(sources) == {"alpha", "beta"}

        alpha = sources["alpha"]
        assert alpha["total"] == 2
        assert sum(alpha["tiers"].values()) == alpha["total"]
        assert abs(alpha["mean_trust_score"] - 0.80) <= 0.001
        assert abs(alpha["mean_scan_count"] - 2.0) <= 0.001

        beta = sources["beta"]
        assert beta["total"] == 2
        assert sum(beta["tiers"].values()) == beta["total"]
        assert abs(beta["mean_trust_score"] - 0.55) <= 0.001
        assert abs(beta["mean_scan_count"] - 3.0) <= 0.001

        assert sum(
            len(source["tiers"]) for source in payload["sources"]
        ) >= 3

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()