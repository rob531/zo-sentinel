"""logic.py -- registry trust-score distribution computation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _bin_index(score: float | int | Decimal) -> int:
    """Return the zero-based decile index for a trust score."""
    try:
        scaled = Decimal(str(score)) * 10
    except (InvalidOperation, TypeError, ValueError):
        return 0

    if scaled <= 0:
        return 0
    if scaled >= 100:
        return 9
    return int(scaled)


def trust_score_distribution(db: Session) -> Dict[str, Any]:
    """Compute the registry trust-score distribution grouped by verdict."""
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
        )
    ).all()

    bins: List[Dict[str, Any]] = [
        {
            "bin": index,
            "min": index / 10,
            "max": (index + 1) / 10,
            "count": 0,
            "verdicts": {},
        }
        for index in range(10)
    ]

    null_count = 0

    for _server_id, score, verdict in rows:
        if score is None:
            null_count += 1
            continue

        index = _bin_index(score)
        bucket = bins[index]
        bucket["count"] += 1

        verdict_key = verdict if verdict is not None else "unknown"
        bucket["verdicts"][verdict_key] = bucket["verdicts"].get(verdict_key, 0) + 1

    return {
        "total": len(rows),
        "bins": bins,
        "null_count": null_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_trust_score_distribution(db: Session) -> Dict[str, Any]:
    """Return the trust-score distribution for the registry."""
    return trust_score_distribution(db)


def __main__() -> None:
    from fastapi import Depends, FastAPI
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

    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = TestingSessionLocal()

    verdicts = ("trusted", "suspicious", "malicious")
    for index in range(10):
        db.add(
            McpServerRegistry(
                server_id=f"server-{index}",
                trust_score=index / 10,
                verdict=verdicts[index % len(verdicts)],
            )
        )
    db.add(McpServerRegistry(server_id="server-null-1", trust_score=None, verdict="unknown"))
    db.add(McpServerRegistry(server_id="server-null-2", trust_score=None, verdict="unknown"))
    db.commit()

    def override_get_session():
        yield db

    that_app = FastAPI()

    @that_app.get("/api/registry/trust-score-distribution")
    def trust_score_distribution_endpoint(session=Depends(get_session)):
        return get_trust_score_distribution(session)

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/registry/trust-score-distribution")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert sum(bucket["count"] for bucket in payload["bins"]) == 10
    assert payload["null_count"] == 2

    db.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    __main__()