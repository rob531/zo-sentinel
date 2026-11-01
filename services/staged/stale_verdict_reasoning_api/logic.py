from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _days_since_assessed(
    last_assessed: datetime | None,
    now: datetime | None = None,
) -> int | None:
    if last_assessed is None:
        return None

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    assessed = last_assessed
    if assessed.tzinfo is None:
        assessed = assessed.replace(tzinfo=timezone.utc)
    else:
        assessed = assessed.astimezone(timezone.utc)

    elapsed = current - assessed
    return max(0, int(elapsed.total_seconds() // 86400))


def get_stale_verdict_reasoning(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
    ).mappings().all()

    total_servers = len(rows)
    grouped: dict[tuple[Any, Any], dict[str, int]] = {}
    missing_rows: list[dict[str, Any]] = []

    for row in rows:
        reasoning = row["verdict_reasoning"]
        missing = reasoning is None or not str(reasoning).strip()
        key = (row["risk_tier"], row["verdict"])
        group = grouped.setdefault(key, {"servers": 0, "missing_reasoning": 0})
        group["servers"] += 1

        if missing:
            group["missing_reasoning"] += 1
            days = _days_since_assessed(row["last_assessed"], now)
            missing_rows.append(
                {
                    "server_id": row["server_id"],
                    "name": row["name"],
                    "verdict": row["verdict"],
                    "risk_tier": row["risk_tier"],
                    "days_since_assessed": days,
                }
            )

    risk_tier_totals: dict[Any, dict[str, int]] = {}
    for (risk_tier, _verdict), counts in grouped.items():
        tier = risk_tier_totals.setdefault(
            risk_tier,
            {"servers": 0, "missing_reasoning": 0},
        )
        tier["servers"] += counts["servers"]
        tier["missing_reasoning"] += counts["missing_reasoning"]

    by_risk_tier = []
    for risk_tier in sorted(
        risk_tier_totals,
        key=lambda value: (value is None, str(value)),
    ):
        counts = risk_tier_totals[risk_tier]
        by_risk_tier.append(
            {
                "risk_tier": risk_tier,
                "servers": counts["servers"],
                "missing_reasoning": counts["missing_reasoning"],
                "missing_pct": round(
                    counts["missing_reasoning"] * 100.0 / counts["servers"],
                    1,
                )
                if counts["servers"]
                else 0.0,
            }
        )

    missing_count = len(missing_rows)
    dated_missing = [row for row in missing_rows if row["days_since_assessed"] is not None]
    dated_missing.sort(
        key=lambda row: (-row["days_since_assessed"], str(row["server_id"]))
    )

    return {
        "totals": {
            "servers": total_servers,
            "missing_reasoning": missing_count,
            "missing_pct": round(missing_count * 100.0 / total_servers, 1)
            if total_servers
            else 0.0,
        },
        "by_risk_tier": by_risk_tier,
        "oldest": dated_missing,
    }


get_stale_verdict_reasoning_data = get_stale_verdict_reasoning
stale_verdict_reasoning = get_stale_verdict_reasoning


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    testing_session = sessionmaker(bind=test_engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    fixtures = [
        {
            "server_id": "old-missing",
            "name": "Old Missing",
            "verdict": "malicious",
            "verdict_reasoning": None,
            "confidence": 0.9,
            "risk_tier": "critical",
            "last_assessed": now.replace(year=now.year - 1),
            "last_seen": now,
        },
        {
            "server_id": "empty-missing",
            "name": "Empty Missing",
            "verdict": "suspicious",
            "verdict_reasoning": "",
            "confidence": 0.7,
            "risk_tier": "high",
            "last_assessed": now,
            "last_seen": now,
        },
        {
            "server_id": "reasoned-one",
            "name": "Reasoned One",
            "verdict": "benign",
            "verdict_reasoning": "Documented evidence.",
            "confidence": 0.8,
            "risk_tier": "low",
            "last_assessed": now,
            "last_seen": now,
        },
        {
            "server_id": "reasoned-two",
            "name": "Reasoned Two",
            "verdict": "benign",
            "verdict_reasoning": "Additional evidence.",
            "confidence": 0.8,
            "risk_tier": "medium",
            "last_assessed": now,
            "last_seen": now,
        },
        {
            "server_id": "reasoned-three",
            "name": "Reasoned Three",
            "verdict": "unknown",
            "verdict_reasoning": "Known source and behavior.",
            "confidence": 0.5,
            "risk_tier": "medium",
            "last_assessed": now,
            "last_seen": now,
        },
    ]
    testing_session.add_all(McpServerRegistry(**fixture) for fixture in fixtures)
    testing_session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/stale-verdict-reasoning")
    def endpoint(payload: dict[str, Any] = Depends(get_stale_verdict_reasoning)):
        return payload

    response = TestClient(test_app).get(
        "/api/registry/stale-verdict-reasoning"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["missing_reasoning"] == 2
    assert any(
        server["server_id"] == "old-missing" for server in payload["oldest"]
    )

    testing_session.close()
    test_engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()