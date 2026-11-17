from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select

from app.db import get_session
from app.models import McpServerRegistry

COHORT_ORDER = ("0-7d", "8-30d", "31-90d", "91-365d", "365d+")


class VerdictAgeCohortRows(list[dict[str, Any]]):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        days: int,
        generated_at: datetime,
        totals: dict[str, Any],
    ) -> None:
        super().__init__(rows)
        self.days = days
        self.generated_at = generated_at
        self.totals = totals
        self.payload = {
            "days": days,
            "generated_at": generated_at.isoformat(),
            "cohorts": rows,
            "totals": totals,
        }

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self.payload[key]
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def keys(self):
        return self.payload.keys()

    def items(self):
        return self.payload.items()


async def _execute(session: Any, statement: Any) -> Any:
    result = session.execute(statement)
    if inspect.isawaitable(result):
        result = await result
    return result


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Expected a datetime value")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cohort_for_age(age_days: float) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    if age_days <= 365:
        return "91-365d"
    return "365d+"


async def get_verdict_age_cohorts(
    session: Any,
    days: int = 30,
) -> VerdictAgeCohortRows:
    if days < 0:
        raise ValueError("days must be non-negative")

    clock_result = await _execute(session, select(func.current_timestamp()))
    clock = clock_result.scalar()
    generated_at = _utc_datetime(clock or datetime.now(timezone.utc))

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed,
        McpServerRegistry.last_seen,
        McpServerRegistry.scan_count,
    )
    result = await _execute(session, statement)
    records = result.all()

    counts: dict[tuple[str, str], int] = {}
    totals_by_verdict: dict[str, int] = {}
    total = 0

    for record in records:
        first_seen = record._mapping["first_seen"]
        if first_seen is None:
            continue

        age_seconds = (generated_at - _utc_datetime(first_seen)).total_seconds()
        age_days = max(0.0, age_seconds / 86400.0)
        if age_days > days:
            continue

        cohort = _cohort_for_age(age_days)
        verdict_value = record._mapping["verdict"]
        verdict = str(verdict_value) if verdict_value is not None else "unknown"
        key = (cohort, verdict)
        counts[key] = counts.get(key, 0) + 1
        totals_by_verdict[verdict] = totals_by_verdict.get(verdict, 0) + 1
        total += 1

    rows = [
        {"cohort": cohort, "verdict": verdict, "count": counts[(cohort, verdict)]}
        for cohort in COHORT_ORDER
        for verdict in sorted(
            verdict for cohort_name, verdict in counts if cohort_name == cohort
        )
    ]
    totals = {"total": total, "by_verdict": totals_by_verdict}
    return VerdictAgeCohortRows(rows, days, generated_at, totals)


async def get_verdict_age_cohort_summary(
    session: Any,
    days: int = 30,
) -> dict[str, Any]:
    return (await get_verdict_age_cohorts(session, days)).payload


async def get_verdict_age_cohort_data(
    session: Any,
    days: int = 30,
) -> VerdictAgeCohortRows:
    return await get_verdict_age_cohorts(session, days)


async def get_registry_verdict_age_cohorts(
    session: Any,
    days: int = 30,
) -> VerdictAgeCohortRows:
    return await get_verdict_age_cohorts(session, days)


async def calculate_verdict_age_cohorts(
    session: Any,
    days: int = 30,
) -> VerdictAgeCohortRows:
    return await get_verdict_age_cohorts(session, days)


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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc)
    seeded = (
        McpServerRegistry(
            server_id="recent-server",
            name="recent-server",
            verdict="safe",
            risk_tier="low",
            first_seen=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="medium-server",
            name="medium-server",
            verdict="caution",
            risk_tier="medium",
            first_seen=now - timedelta(days=20),
        ),
        McpServerRegistry(
            server_id="old-server",
            name="old-server",
            verdict="blocked",
            risk_tier="high",
            first_seen=now - timedelta(days=100),
        ),
    )

    with SessionLocal() as session:
        session.add_all(seeded)
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/verdict-age-cohorts")
    async def verdict_age_cohorts(
        days: int = 30,
        session: Any = Depends(get_session),
    ) -> dict[str, Any]:
        return await get_verdict_age_cohort_summary(session, days)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/verdict-age-cohorts",
            params={"days": 365},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 365
        assert payload["generated_at"]
        assert payload["cohorts"]
        assert all(entry["count"] >= 0 for entry in payload["cohorts"])

        recent = [
            entry for entry in payload["cohorts"] if entry["cohort"] == "0-7d"
        ]
        assert len(recent) == 1
        assert recent[0]["verdict"] == "safe"
        assert recent[0]["count"] == 1
        assert payload["totals"]["total"] == 3
        assert payload["totals"]["by_verdict"] == {
            "blocked": 1,
            "caution": 1,
            "safe": 1,
        }

    print("PASS")