from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


STALE_THRESHOLD_HOURS = 24


def _grouped_counts(session: Session, column) -> dict[str, int]:
    statement = select(column, func.count().label("count")).group_by(column)
    return {
        ("unknown" if value is None else str(value)): int(count)
        for value, count in session.execute(statement).all()
    }


def _registry_distribution(session: Session, registry_source: str | None) -> dict:
    table = McpServerRegistry.__table__
    filters = []

    if registry_source is not None:
        filters.append(table.c.registry_source == registry_source)

    total_statement = select(func.count()).select_from(table)
    never_assessed_statement = select(func.count()).select_from(table).where(
        table.c.last_assessed.is_(None)
    )

    if filters:
        total_statement = total_statement.where(*filters)
        never_assessed_statement = never_assessed_statement.where(*filters)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_THRESHOLD_HOURS)
    if not getattr(table.c.last_assessed.type, "timezone", False):
        cutoff = cutoff.replace(tzinfo=None)

    stale_statement = (
        select(func.count())
        .select_from(table)
        .where(
            table.c.last_assessed.is_not(None),
            table.c.last_assessed < cutoff,
        )
    )
    if filters:
        stale_statement = stale_statement.where(*filters)

    return {
        "total_servers": int(session.execute(total_statement).scalar_one()),
        "by_risk_tier": _grouped_counts(session, table.c.risk_tier),
        "by_verdict": _grouped_counts(session, table.c.verdict),
        "by_registry_source": _grouped_counts(session, table.c.registry_source),
        "never_assessed_count": int(
            session.execute(never_assessed_statement).scalar_one()
        ),
        "stale_count": int(session.execute(stale_statement).scalar_one()),
        "stale_threshold_hours": STALE_THRESHOLD_HOURS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def registry_distribution(
    registry_source: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    if isinstance(session, Session):
        return _registry_distribution(session, registry_source)

    dependency = get_session()
    db = next(dependency)
    try:
        return _registry_distribution(db, registry_source)
    finally:
        try:
            next(dependency)
        except StopIteration:
            pass


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    testing_session = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        McpServerRegistry(
            server_id="registry-self-test-1",
            name="Registry Self Test 1",
            url="https://example.invalid/1",
            verdict="trusted",
            risk_tier="low",
            registry_source="github",
            last_assessed=now,
            trust_score=0.95,
            confidence=0.99,
        ),
        McpServerRegistry(
            server_id="registry-self-test-2",
            name="Registry Self Test 2",
            url="https://example.invalid/2",
            verdict="risky",
            risk_tier="medium",
            registry_source="github",
            last_assessed=now - timedelta(hours=48),
            trust_score=0.55,
            confidence=0.90,
        ),
        McpServerRegistry(
            server_id="registry-self-test-3",
            name="Registry Self Test 3",
            url="https://example.invalid/3",
            verdict="banned",
            risk_tier="high",
            registry_source="npm",
            last_assessed=None,
            trust_score=0.05,
            confidence=0.85,
        ),
        McpServerRegistry(
            server_id="registry-self-test-4",
            name="Registry Self Test 4",
            url="https://example.invalid/4",
            verdict="trusted",
            risk_tier="low",
            registry_source="npm",
            last_assessed=now,
            trust_score=0.93,
            confidence=0.98,
        ),
        McpServerRegistry(
            server_id="registry-self-test-5",
            name="Registry Self Test 5",
            url="https://example.invalid/5",
            verdict="risky",
            risk_tier="medium",
            registry_source="npm",
            last_assessed=now,
            trust_score=0.50,
            confidence=0.92,
        ),
        McpServerRegistry(
            server_id="registry-self-test-6",
            name="Registry Self Test 6",
            url="https://example.invalid/6",
            verdict="banned",
            risk_tier="high",
            registry_source="github",
            last_assessed=now,
            trust_score=0.10,
            confidence=0.94,
        ),
    ]

    with testing_session() as db:
        db.add_all(rows)
        db.commit()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/distribution")
    def endpoint(
        registry_source: str | None = None,
        db: Session = Depends(get_session),
    ):
        return registry_distribution(registry_source, db)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 6
        assert sum(payload["by_risk_tier"].values()) == 6
        assert payload["never_assessed_count"] == 1
        assert len(payload["by_registry_source"]) == 2

        filtered = client.get(
            "/api/registry/distribution",
            params={"registry_source": "github"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total_servers"] == 3

    print("PASS")


if __name__ == "__main__":
    _run_self_test()