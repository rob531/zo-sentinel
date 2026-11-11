from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _bucket(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str) and not value.strip():
        return "null"
    return str(value)


def _dimension(rows: list[tuple[Any, Any]], index: int, total: int) -> list[dict[str, Any]]:
    counts = Counter(_bucket(row[index]) for row in rows)
    result = [
        {
            "value": value,
            "count": count,
            "share_pct": round((count / total) * 100, 2) if total else 0.0,
        }
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return result


def _registry_risk_tier_coverage(session: Session) -> dict[str, Any]:
    rows = list(
        session.execute(
            select(
                McpServerRegistry.risk_tier,
                McpServerRegistry.verdict,
                McpServerRegistry.registry_source,
            )
        ).all()
    )
    total = len(rows)

    dimensions = {
        "risk_tier": _dimension(rows, 0, total),
        "verdict": _dimension(rows, 1, total),
        "registry_source": _dimension(rows, 2, total),
    }

    cross_tab = Counter(
        (_bucket(row[0]), _bucket(row[2]))
        for row in rows
    )
    risk_tier_by_source = [
        {
            "risk_tier": risk_tier,
            "registry_source": registry_source,
            "count": count,
        }
        for (risk_tier, registry_source), count in sorted(
            cross_tab.items(),
            key=lambda item: (item[0][0], item[0][1]),
        )
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total,
        "dimensions": dimensions,
        "risk_tier_by_source": risk_tier_by_source,
    }


def get_registry_risk_tier_coverage(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _registry_risk_tier_coverage(session)


def get_registry_risk_tier_null_share(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _registry_risk_tier_coverage(session)


def get_risk_tier_coverage(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _registry_risk_tier_coverage(session)


def get_risk_tier_distribution(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _registry_risk_tier_coverage(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-1",
                    name="Registry One",
                    registry_source="source_a",
                    risk_tier=None,
                    verdict=None,
                ),
                McpServerRegistry(
                    server_id="registry-2",
                    name="Registry Two",
                    registry_source="source_a",
                    risk_tier="low",
                    verdict="approved",
                ),
                McpServerRegistry(
                    server_id="registry-3",
                    name="Registry Three",
                    registry_source="source_b",
                    risk_tier="medium",
                    verdict="",
                ),
                McpServerRegistry(
                    server_id="registry-4",
                    name="Registry Four",
                    registry_source="source_b",
                    risk_tier=None,
                    verdict=None,
                ),
                McpServerRegistry(
                    server_id="registry-5",
                    name="Registry Five",
                    registry_source="source_a",
                    risk_tier="high",
                    verdict="rejected",
                ),
                McpServerRegistry(
                    server_id="registry-6",
                    name="Registry Six",
                    registry_source="source_b",
                    risk_tier="low",
                    verdict="approved",
                ),
            ]
        )
        test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/risk-tier-coverage")
    def registry_risk_tier_coverage_endpoint(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_registry_risk_tier_coverage(session=session)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/risk-tier-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 6

        risk_tier_buckets = {
            item["value"]: item["count"]
            for item in payload["dimensions"]["risk_tier"]
        }
        assert "null" in risk_tier_buckets
        assert risk_tier_buckets["null"] >= 1

        for dimension in payload["dimensions"].values():
            assert abs(sum(item["share_pct"] for item in dimension) - 100) < 0.1

    print("PASS")