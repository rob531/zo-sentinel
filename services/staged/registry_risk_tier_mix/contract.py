from __future__ import annotations

import math
from typing import List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session


class TierMixRow(BaseModel):
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    count: int
    pct: float


class TierMixResponse(BaseModel):
    total: int
    by_tier: List[TierMixRow]
    by_verdict: List[TierMixRow]
    unscored: int
    registry_source: Optional[str] = None


def _create_test_app() -> FastAPI:
    from services.staged.registry_risk_tier_mix.router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    registry_source TEXT,
                    verdict TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    confidence REAL,
                    last_assessed TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    scan_count INTEGER DEFAULT 0,
                    meta TEXT
                )
                """
            )
        )

    rows = [
        {
            "server_id": "srv-trusted-1",
            "name": "Trusted One",
            "registry_source": "alpha",
            "verdict": "ALLOW",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.97,
            "confidence": 0.99,
            "last_assessed": "2024-01-01T00:00:00Z",
            "first_seen": "2023-01-01T00:00:00Z",
            "last_seen": "2024-01-01T00:00:00Z",
            "scan_count": 3,
            "meta": "{}",
        },
        {
            "server_id": "srv-trusted-2",
            "name": "Trusted Two",
            "registry_source": "alpha",
            "verdict": "ALLOW",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.95,
            "confidence": 0.98,
            "last_assessed": "2024-01-01T00:00:00Z",
            "first_seen": "2023-01-01T00:00:00Z",
            "last_seen": "2024-01-01T00:00:00Z",
            "scan_count": 2,
            "meta": "{}",
        },
        {
            "server_id": "srv-enterprise",
            "name": "Enterprise Server",
            "registry_source": "alpha",
            "verdict": "REVIEW",
            "risk_tier": "ENTERPRISE_CONTROLLED",
            "trust_score": 0.80,
            "confidence": 0.95,
            "last_assessed": "2024-01-01T00:00:00Z",
            "first_seen": "2023-01-01T00:00:00Z",
            "last_seen": "2024-01-01T00:00:00Z",
            "scan_count": 4,
            "meta": "{}",
        },
        {
            "server_id": "srv-caution",
            "name": "Caution Server",
            "registry_source": "alpha",
            "verdict": "BLOCK",
            "risk_tier": "CAUTION_LIMITED",
            "trust_score": 0.40,
            "confidence": 0.92,
            "last_assessed": "2024-01-01T00:00:00Z",
            "first_seen": "2023-01-01T00:00:00Z",
            "last_seen": "2024-01-01T00:00:00Z",
            "scan_count": 1,
            "meta": "{}",
        },
        {
            "server_id": "srv-unscored",
            "name": "Unscored Server",
            "registry_source": "beta",
            "verdict": "PENDING",
            "risk_tier": None,
            "trust_score": None,
            "confidence": None,
            "last_assessed": None,
            "first_seen": "2023-01-01T00:00:00Z",
            "last_seen": "2024-01-01T00:00:00Z",
            "scan_count": 0,
            "meta": "{}",
        },
    ]

    with testing_session.begin() as session:
        for row in rows:
            session.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id,
                        name,
                        registry_source,
                        verdict,
                        risk_tier,
                        trust_score,
                        confidence,
                        last_assessed,
                        first_seen,
                        last_seen,
                        scan_count,
                        meta
                    ) VALUES (
                        :server_id,
                        :name,
                        :registry_source,
                        :verdict,
                        :risk_tier,
                        :trust_score,
                        :confidence,
                        :last_assessed,
                        :first_seen,
                        :last_seen,
                        :scan_count,
                        :meta
                    )
                    """
                ),
                row,
            )

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    route_paths = {getattr(route, "path", "") for route in router.routes}
    if "/api/registry/tier-mix" in route_paths:
        app.include_router(router)
    elif "/registry/tier-mix" in route_paths:
        app.include_router(router, prefix="/api")
    else:
        app.include_router(router, prefix="/api")

    return app


def run() -> None:
    app = _create_test_app()

    with TestClient(app) as client:
        response = client.get("/api/registry/tier-mix")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["total"] == 5, payload
        assert sum(int(row["count"]) for row in payload["by_tier"]) == 4, payload
        assert int(payload["unscored"]) == 1, payload
        assert payload["registry_source"] is None, payload
        assert math.isclose(
            sum(float(row["pct"]) for row in payload["by_tier"]),
            100.0,
            rel_tol=0.0,
            abs_tol=0.25,
        ), payload

        tier_counts = {
            row["risk_tier"]: int(row["count"]) for row in payload["by_tier"]
        }
        assert tier_counts == {
            "TRUSTED_GENERAL": 2,
            "ENTERPRISE_CONTROLLED": 1,
            "CAUTION_LIMITED": 1,
        }, payload

        filtered_response = client.get(
            "/api/registry/tier-mix",
            params={"registry_source": "alpha"},
        )
        assert filtered_response.status_code == 200, filtered_response.text
        filtered = filtered_response.json()

        assert filtered["total"] == 4, filtered
        assert filtered["unscored"] == 0, filtered
        assert filtered["registry_source"] == "alpha", filtered
        assert sum(int(row["count"]) for row in filtered["by_tier"]) == 4, filtered
        assert math.isclose(
            sum(float(row["pct"]) for row in filtered["by_tier"]),
            100.0,
            rel_tol=0.0,
            abs_tol=0.25,
        ), filtered

    print("PASS")


if __name__ == "__main__":
    run()