from __future__ import annotations

from statistics import median
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_reasoning_profile(
    session: Session = Depends(get_session),
    verdict: str | None = None,
    min_chars: int = 0,
) -> dict[str, Any]:
    if min_chars < 0:
        raise ValueError("min_chars must be non-negative")

    statement = select(
        McpServerRegistry.verdict,
        McpServerRegistry.verdict_reasoning,
    )
    if verdict is not None:
        statement = statement.where(McpServerRegistry.verdict == verdict)

    rows = session.execute(statement).all()
    grouped: dict[str, list[str]] = {}

    for row_verdict, reasoning in rows:
        key = "" if row_verdict is None else str(row_verdict)
        grouped.setdefault(key, []).append("" if reasoning is None else str(reasoning))

    profiles: list[dict[str, Any]] = []
    for group_verdict in sorted(grouped):
        reasons = grouped[group_verdict]
        lengths = [len(reasoning) for reasoning in reasons]
        with_reasoning = sum(1 for reasoning in reasons if reasoning != "")

        profiles.append(
            {
                "verdict": group_verdict,
                "servers": len(reasons),
                "with_reasoning": with_reasoning,
                "missing_reasoning": len(reasons) - with_reasoning,
                "avg_chars": sum(lengths) / len(lengths),
                "median_chars": float(median(lengths)),
                "min_chars": min(lengths),
                "max_chars": max(lengths),
                "under_min_chars": sum(1 for length in lengths if length < min_chars),
            }
        )

    return {
        "total": sum(profile["servers"] for profile in profiles),
        "verdicts": profiles,
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    seeds: list[dict[str, Any]] = [
        {
            "server_id": "profile-safe-empty",
            "verdict": "safe",
            "verdict_reasoning": "",
        },
        {
            "server_id": "profile-safe-abc",
            "verdict": "safe",
            "verdict_reasoning": "abc",
        },
        {
            "server_id": "profile-safe-long",
            "verdict": "safe",
            "verdict_reasoning": "s" * 40,
        },
        {
            "server_id": "profile-risky-empty",
            "verdict": "risky",
            "verdict_reasoning": "",
        },
        {
            "server_id": "profile-risky-short",
            "verdict": "risky",
            "verdict_reasoning": "r" * 7,
        },
        {
            "server_id": "profile-risky-long",
            "verdict": "risky",
            "verdict_reasoning": "x" * 40,
        },
    ]

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session_factory() as session:
        session.add_all(McpServerRegistry(**seed) for seed in seeds)
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/verdict-reasoning-profile")
    def verdict_reasoning_profile_endpoint(
        verdict: str | None = None,
        min_chars: int = 0,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_verdict_reasoning_profile(
            session=session,
            verdict=verdict,
            min_chars=min_chars,
        )

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/verdict-reasoning-profile",
            params={"min_chars": 10},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 6
        by_verdict = {item["verdict"]: item for item in payload["verdicts"]}
        assert by_verdict["safe"]["missing_reasoning"] == 1
        assert by_verdict["risky"]["missing_reasoning"] == 1
        assert by_verdict["safe"]["median_chars"] == 3
        assert by_verdict["risky"]["median_chars"] == 7

    print("PASS")