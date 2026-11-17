from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from app.db import get_session
from app.models import McpServerRegistry


def get_trust_score_drift(session: Any, days: int = 30) -> dict[str, Any]:
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_date = (now - timedelta(days=days - 1)).date()
    start_datetime = datetime.combine(start_date, datetime.min.time())

    rows = (
        session.query(
            McpServerRegistry.last_assessed,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
        )
        .filter(McpServerRegistry.last_assessed >= start_datetime)
        .filter(McpServerRegistry.last_assessed <= now)
        .all()
    )

    grouped: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
    for last_assessed, trust_score, verdict in rows:
        if last_assessed is None:
            continue
        if hasattr(last_assessed, "date"):
            bucket = last_assessed.date().isoformat()
        else:
            bucket = str(last_assessed)[:10]
        grouped[bucket].append((trust_score, verdict))

    series: list[dict[str, Any]] = []
    for bucket in sorted(grouped):
        entries = grouped[bucket]
        scores = [
            float(trust_score)
            for trust_score, _ in entries
            if trust_score is not None
        ]
        verdicts = Counter(
            str(verdict)
            for _, verdict in entries
            if verdict is not None
        )

        series.append(
            {
                "date": bucket,
                "scored_servers": len(entries),
                "min_trust": min(scores) if scores else None,
                "avg_trust": (sum(scores) / len(scores)) if scores else None,
                "max_trust": max(scores) if scores else None,
                "verdicts": dict(sorted(verdicts.items())),
            }
        )

    return {"days": days, "series": series}


def get_score_drift(session: Any, days: int = 30) -> dict[str, Any]:
    return get_trust_score_drift(session, days)


def get_trust_drift(session: Any, days: int = 30) -> dict[str, Any]:
    return get_trust_score_drift(session, days)


def get_trust_drift_data(session: Any, days: int = 30) -> dict[str, Any]:
    return get_trust_score_drift(session, days)


def get_drift_data(session: Any, days: int = 30) -> dict[str, Any]:
    return get_trust_score_drift(session, days)


def calculate_trust_drift(session: Any, days: int = 30) -> dict[str, Any]:
    return get_trust_score_drift(session, days)


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    testing_session = session_factory()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = now - timedelta(days=1)

    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="drift-contract-1",
                name="Drift Contract One",
                trust_score=0.9,
                verdict="VERIFIED",
                risk_tier="LOW",
                last_assessed=now,
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="drift-contract-2",
                name="Drift Contract Two",
                trust_score=0.4,
                verdict="REVIEW",
                risk_tier="HIGH",
                last_assessed=now,
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="drift-contract-3",
                name="Drift Contract Three",
                trust_score=0.7,
                verdict="VERIFIED",
                risk_tier="MEDIUM",
                last_assessed=yesterday,
                last_seen=yesterday,
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/trust/drift")
    def trust_drift_endpoint(days: int = 30, session: Any = Depends(get_session)):
        return get_trust_score_drift(session, days)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/trust/drift", params={"days": 2})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 2
        series = payload["series"]
        assert len(series) == len({item["date"] for item in series}) == 2
        multi_server_bucket = next(
            item for item in series if item["scored_servers"] == 2
        )
        assert abs(multi_server_bucket["avg_trust"] - ((0.9 + 0.4) / 2)) < 1e-9
        assert multi_server_bucket["verdicts"]
        assert any("VERIFIED" in item["verdicts"] for item in series)

    testing_session.close()
    engine.dispose()
    print("PASS")