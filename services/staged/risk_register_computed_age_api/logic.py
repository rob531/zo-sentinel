from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_NAMES = ("<1h", "1-24h", "1-7d", ">7d")


def _parse_computed_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    else:
        raise ValueError(f"Unsupported computed_at value: {value!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_for_age(age: timedelta) -> str:
    if age < timedelta(hours=1):
        return "<1h"
    if age <= timedelta(hours=24):
        return "1-24h"
    if age <= timedelta(days=7):
        return "1-7d"
    return ">7d"


def get_risk_register_computed_age(
    session: Optional[Session] = Depends(get_session),
    db: Optional[Session] = None,
) -> dict:
    active_session = db if db is not None else session
    if active_session is None:
        raise HTTPException(status_code=503, detail="Database session unavailable")

    result = active_session.execute(
        text("SELECT computed_at FROM mcp_risk_register")
    )
    computed_values = result.scalars().all()

    now = datetime.now(timezone.utc)
    counts = {bucket: 0 for bucket in _BUCKET_NAMES}
    parsed_values = []

    for value in computed_values:
        if value is None:
            continue
        parsed = _parse_computed_at(value)
        parsed_values.append(parsed)
        counts[_bucket_for_age(now - parsed)] += 1

    oldest = min(parsed_values) if parsed_values else None
    newest = max(parsed_values) if parsed_values else None

    stale_over_24h = sum(
        1 for parsed in parsed_values if now - parsed > timedelta(hours=24)
    )

    return {
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in _BUCKET_NAMES
        ],
        "total_entries": len(computed_values),
        "oldest_computed_at": oldest.isoformat() if oldest else None,
        "newest_computed_at": newest.isoformat() if newest else None,
        "stale_over_24h": stale_over_24h,
    }


get_computed_age_distribution = get_risk_register_computed_age
get_risk_register_age_distribution = get_risk_register_computed_age
get_risk_register_computed_age_distribution = get_risk_register_computed_age
get_age_distribution = get_risk_register_computed_age
get_risk_register_age = get_risk_register_computed_age


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    mcp_name TEXT PRIMARY KEY,
                    score REAL,
                    verdict TEXT,
                    computed_at TEXT NOT NULL
                )
                """
            )
        )

    current_time = datetime.now(timezone.utc)
    rows = [
        ("entry-now", 90.0, "critical", current_time),
        ("entry-2h", 70.0, "warning", current_time - timedelta(hours=2)),
        ("entry-3d-1", 60.0, "warning", current_time - timedelta(days=3)),
        ("entry-3d-2", 50.0, "safe", current_time - timedelta(days=3)),
        ("entry-3d-3", 40.0, "safe", current_time - timedelta(days=3)),
    ]

    with session_factory() as session:
        for mcp_name, score, verdict, computed_at in rows:
            session.execute(
                text(
                    """
                    INSERT INTO mcp_risk_register
                        (mcp_name, score, verdict, computed_at)
                    VALUES (:mcp_name, :score, :verdict, :computed_at)
                    """
                ),
                {
                    "mcp_name": mcp_name,
                    "score": score,
                    "verdict": verdict,
                    "computed_at": computed_at.isoformat(),
                },
            )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/risk-register/age")
    def risk_register_age(session: Session = Depends(get_session)):
        return get_risk_register_computed_age(session=session)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk-register/age")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_entries"] == 5
        assert payload["stale_over_24h"] == 3
        buckets = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert buckets[">7d"] == 0
        assert buckets["<1h"] == 1

    test_app.dependency_overrides.clear()
    engine.dispose()
    print("PASS")