from collections import defaultdict
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnLink

router = APIRouter(prefix="/api")


def _clamp(value: float, lower: float, upper: float) -> float:
    if value != value:
        return lower
    return max(lower, min(upper, value))


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@router.get("/vuln/links/match-confidence")
def get_match_confidence(
    min_confidence: float = Query(0.0),
    limit: int = Query(50),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    minimum = _clamp(min_confidence, 0.0, 1.0)
    maximum = _clamp_int(limit, 1, 500)

    statement = (
        select(
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_confidence,
            VulnLink.linked_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            VulnLink.server_id == McpServerRegistry.server_id,
        )
        .where(VulnLink.match_confidence >= minimum)
    )
    rows = session.execute(statement).all()

    basis_values: dict[str, list[float]] = defaultdict(list)
    tier_counts: dict[str, int] = defaultdict(int)
    low_confidence_links: list[dict[str, Any]] = []

    for row in rows:
        confidence = float(row.match_confidence or 0.0)
        basis = str(row.match_basis)
        tier = str(row.risk_tier)

        basis_values[basis].append(confidence)
        tier_counts[tier] += 1

        if confidence < 0.5:
            low_confidence_links.append(
                {
                    "advisory_id": str(row.advisory_id),
                    "server_id": str(row.server_id),
                    "server_name": str(row.name),
                    "match_basis": basis,
                    "match_confidence": confidence,
                    "linked_at": _isoformat(row.linked_at),
                }
            )

    by_basis = []
    for basis, values in basis_values.items():
        by_basis.append(
            {
                "match_basis": basis,
                "count": len(values),
                "avg_confidence": sum(values) / len(values),
                "min_confidence": min(values),
                "max_confidence": max(values),
            }
        )
    by_basis.sort(key=lambda item: (-item["count"], item["match_basis"]))

    low_confidence_links.sort(
        key=lambda item: (item["match_confidence"], item["server_id"])
    )

    tier_distribution = [
        {"risk_tier": tier, "count": count}
        for tier, count in sorted(tier_counts.items())
    ]

    return {
        "by_basis": by_basis,
        "tier_distribution": tier_distribution,
        "low_confidence_links": low_confidence_links[:maximum],
        "total_matched": len(rows),
    }


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, VulnLink.__table__],
    )

    session = Session(engine)
    try:
        session.execute(
            McpServerRegistry.__table__.insert(),
            [
                {
                    "server_id": "server-a",
                    "name": "Server A",
                    "risk_tier": "critical",
                },
                {
                    "server_id": "server-b",
                    "name": "Server B",
                    "risk_tier": "high",
                },
            ],
        )
        session.execute(
            VulnLink.__table__.insert(),
            [
                {
                    "advisory_id": "CVE-2024-0001",
                    "server_id": "server-a",
                    "match_basis": "cpe",
                    "match_value": "cpe:/a:example:server",
                    "match_confidence": 0.9,
                    "linked_at": datetime(2024, 1, 1, 12, 0, 0),
                },
                {
                    "advisory_id": "CVE-2024-0002",
                    "server_id": "server-b",
                    "match_basis": "cpe",
                    "match_value": "cpe:/a:example:other",
                    "match_confidence": 0.7,
                    "linked_at": datetime(2024, 1, 2, 12, 0, 0),
                },
                {
                    "advisory_id": "CVE-2024-0003",
                    "server_id": "server-a",
                    "match_basis": "advisory",
                    "match_value": "advisory-match",
                    "match_confidence": 0.3,
                    "linked_at": datetime(2024, 1, 3, 12, 0, 0),
                },
                {
                    "advisory_id": "CVE-2024-0004",
                    "server_id": "server-b",
                    "match_basis": "advisory",
                    "match_value": "advisory-match",
                    "match_confidence": 0.4,
                    "linked_at": datetime(2024, 1, 4, 12, 0, 0),
                },
            ],
        )
        session.commit()
    finally:
        session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with Session(engine) as override_session:
            yield override_session

    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/match-confidence")
        assert response.status_code == 200
        payload = response.json()

        assert {entry["match_basis"] for entry in payload["by_basis"]} == {
            "cpe",
            "advisory",
        }
        assert {entry["match_basis"]: entry["count"] for entry in payload["by_basis"]} == {
            "cpe": 2,
            "advisory": 2,
        }
        cpe_entry = next(
            entry for entry in payload["by_basis"] if entry["match_basis"] == "cpe"
        )
        assert abs(cpe_entry["avg_confidence"] - ((0.9 + 0.7) / 2)) < 1e-9
        assert sum(entry["count"] for entry in payload["tier_distribution"]) == payload[
            "total_matched"
        ]
        assert all(
            entry["match_confidence"] < 0.5
            for entry in payload["low_confidence_links"]
        )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()