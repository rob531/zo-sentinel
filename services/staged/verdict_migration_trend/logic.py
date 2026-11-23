"""Logic for the verdict migration trend service."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


ALLOWED_VERDICTS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)
_ALLOWED_VERDICT_SET = set(ALLOWED_VERDICTS)


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def _as_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _registry_identity(name: Optional[str], server_id: Optional[str]) -> str:
    """Use the registry name as the server identity when available.

    Registry imports can retain the same logical server under different
    server_id values; the name is the stable identity in that case.
    """
    if name:
        return f"name:{name}"
    return f"server:{server_id or ''}"


def get_verdict_migration_trend(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return verdict changes and the transition matrix for the requested window.

    The registry contains dated assessment records.  Records are compared with
    the preceding calendar day's record for the same logical server.
    """
    normalized_days = max(1, int(days))
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=normalized_days - 1)

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.last_assessed,
        )
    ).all()

    records: Dict[Tuple[str, date], Tuple[datetime, Optional[str], Optional[str]]] = {}
    latest_by_identity: Dict[str, Tuple[datetime, Optional[str]]] = {}

    for server_id, name, verdict, last_assessed in rows:
        assessed_date = _as_date(last_assessed)
        if assessed_date is None or assessed_date < first_day or assessed_date > today:
            continue
        if verdict not in _ALLOWED_VERDICT_SET:
            continue

        identity = _registry_identity(name, server_id)
        timestamp = _as_timestamp(last_assessed)
        key = (identity, assessed_date)
        previous = records.get(key)
        if previous is None or timestamp >= previous[0]:
            records[key] = (timestamp, verdict, server_id)

        current = latest_by_identity.get(identity)
        if current is None or timestamp >= current[0]:
            latest_by_identity[identity] = (timestamp, verdict)

    transition_counts: Dict[Tuple[date, str, str], int] = {}
    for (identity, assessed_date), (_, current_verdict, _) in records.items():
        previous_record = records.get((identity, assessed_date - timedelta(days=1)))
        if previous_record is None:
            continue
        previous_verdict = previous_record[1]
        if previous_verdict is None or previous_verdict == current_verdict:
            continue
        transition_counts[(assessed_date, previous_verdict, current_verdict)] = (
            transition_counts.get((assessed_date, previous_verdict, current_verdict), 0) + 1
        )

    transitions = [
        {
            "date": assessed_date.isoformat(),
            "from_verdict": from_verdict,
            "to_verdict": to_verdict,
            "count": count,
        }
        for (assessed_date, from_verdict, to_verdict), count in sorted(
            transition_counts.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2]),
        )
    ]

    by_verdict: Dict[str, int] = {}
    for _, verdict in latest_by_identity.values():
        if verdict in _ALLOWED_VERDICT_SET:
            by_verdict[verdict] = by_verdict.get(verdict, 0) + 1

    return {
        "days": normalized_days,
        "transitions": transitions,
        "total_migrations": sum(item["count"] for item in transitions),
        "by_verdict": by_verdict,
    }


def get_migration_trend(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name for the migration trend computation."""
    return get_verdict_migration_trend(db, days)


def get_verdict_migration(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name for the migration trend computation."""
    return get_verdict_migration_trend(db, days)


def get_verdict_trend(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name for the migration trend computation."""
    return get_verdict_migration_trend(db, days)


if __name__ == "__main__":
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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    test_db = TestingSession()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="migration-demo-1",
                name="migration-demo",
                verdict="TRUSTED_GENERAL",
                last_assessed=now - timedelta(days=1),
            ),
            McpServerRegistry(
                server_id="migration-demo-2",
                name="migration-demo",
                verdict="CAUTION_LIMITED",
                last_assessed=now,
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/verdicts/migration")
    def migration_endpoint(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_verdict_migration_trend(db, days)

    def override_get_session() -> Iterable[Session]:
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/verdicts/migration?days=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_migrations"] >= 1
    for transition in payload["transitions"]:
        assert transition["from_verdict"] in ALLOWED_VERDICTS
        assert transition["to_verdict"] in ALLOWED_VERDICTS
    print("PASS")