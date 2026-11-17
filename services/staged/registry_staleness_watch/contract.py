from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")
app = FastAPI()
app.include_router(router)

_current_session: ContextVar = ContextVar("registry_staleness_watch_session", default=None)
_bucket_labels = ("<24h", "1-3d", "3-7d", "7-30d", ">30d")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _acquire_session() -> tuple[Any, bool]:
    session = _current_session.get()
    if session is not None:
        return session, False

    dependency = get_session()
    if hasattr(dependency, "__next__"):
        return next(dependency), True
    return dependency, False


def _release_session(session: Any, owned: bool) -> None:
    if owned:
        close = getattr(session, "close", None)
        if close is not None:
            close()


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: Any) -> Optional[str]:
    timestamp = _as_utc(value)
    if timestamp is None:
        return None
    return timestamp.isoformat().replace("+00:00", "Z")


def _bucket_for(age_hours: float) -> str:
    if age_hours < 24:
        return "<24h"
    if age_hours < 72:
        return "1-3d"
    if age_hours < 168:
        return "3-7d"
    if age_hours < 720:
        return "7-30d"
    return ">30d"


def _collect_stale(
    session: Any,
    threshold_hours: int,
    limit: int,
) -> list[dict[str, Any]]:
    threshold_hours = _clamp(threshold_hours, 1, 8760)
    limit = _clamp(limit, 1, 1000)
    now = datetime.now(timezone.utc)

    rows = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
        McpServerRegistry.trust_score,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
    ).all()

    servers: list[dict[str, Any]] = []
    for row in rows:
        last_seen = _as_utc(row.last_seen)
        last_scanned = _as_utc(row.last_scanned)
        reference = last_seen if last_seen is not None else last_scanned
        if reference is None:
            continue

        age_hours = (now - reference).total_seconds() / 3600.0
        if age_hours <= threshold_hours:
            continue

        trust_score = row.trust_score
        if trust_score is not None:
            trust_score = float(trust_score)

        servers.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "registry_source": row.registry_source,
                "risk_tier": row.risk_tier,
                "verdict": row.verdict,
                "trust_score": trust_score,
                "last_seen": _iso_utc(row.last_seen),
                "last_scanned": _iso_utc(row.last_scanned),
                "age_hours": float(age_hours),
            }
        )

    servers.sort(key=lambda item: (-item["age_hours"], str(item["server_id"])))
    return servers[:limit]


def _bucket_counts(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = {label: 0 for label in _bucket_labels}
    for server in servers:
        counts[_bucket_for(server["age_hours"])] += 1
    return [
        {"bucket": label, "count": counts[label]}
        for label in _bucket_labels
        if counts[label] > 0
    ]


def stale_servers(threshold_hours: int, limit: int) -> dict[str, Any]:
    threshold_hours = _clamp(threshold_hours, 1, 8760)
    limit = _clamp(limit, 1, 1000)
    session, owned = _acquire_session()
    try:
        servers = _collect_stale(session, threshold_hours, limit)
    finally:
        _release_session(session, owned)

    return {
        "threshold_hours": threshold_hours,
        "count": len(servers),
        "buckets": _bucket_counts(servers),
        "servers": servers,
    }


def staleness_buckets(threshold_hours: int) -> list[dict[str, Any]]:
    threshold_hours = _clamp(threshold_hours, 1, 8760)
    session, owned = _acquire_session()
    try:
        servers = _collect_stale(session, threshold_hours, 1000)
    finally:
        _release_session(session, owned)
    return _bucket_counts(servers)


@router.get("/registry/staleness-watch")
def registry_staleness_watch(
    threshold_hours: int = 72,
    limit: int = 200,
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    threshold_hours = _clamp(threshold_hours, 1, 8760)
    limit = _clamp(limit, 1, 1000)
    servers = _collect_stale(session, threshold_hours, limit)
    return {
        "threshold_hours": threshold_hours,
        "count": len(servers),
        "buckets": _bucket_counts(servers),
        "servers": servers,
    }


def _run_self_test() -> None:
    from sqlalchemy import create_engine, text
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
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR(255) PRIMARY KEY,
                    name VARCHAR(255),
                    registry_source VARCHAR(255),
                    risk_tier VARCHAR(255),
                    verdict VARCHAR(255),
                    trust_score FLOAT,
                    last_seen DATETIME,
                    last_scanned DATETIME
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        rows = [
            ("fresh", "Fresh", "test", "low", "trusted", 95.0, 0),
            ("day-old", "Day Old", "test", "medium", "review", 70.0, 48),
            ("week-old", "Week Old", "test", "high", "blocked", 40.0, 100),
            ("ancient", "Ancient", "test", "critical", "blocked", 10.0, 800),
        ]
        for server_id, name, source, tier, verdict, trust_score, age_hours in rows:
            observed = now - timedelta(hours=age_hours)
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id, name, registry_source, risk_tier, verdict,
                        trust_score, last_seen, last_scanned
                    ) VALUES (
                        :server_id, :name, :registry_source, :risk_tier, :verdict,
                        :trust_score, :last_seen, :last_scanned
                    )
                    """
                ),
                {
                    "server_id": server_id,
                    "name": name,
                    "registry_source": source,
                    "risk_tier": tier,
                    "verdict": verdict,
                    "trust_score": trust_score,
                    "last_seen": observed,
                    "last_scanned": observed,
                },
            )

    def test_get_session() -> Iterator[Any]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = test_get_session

    session = session_factory()
    token = _current_session.set(session)
    try:
        result = stale_servers(72, 200)
        buckets = staleness_buckets(72)
    finally:
        _current_session.reset(token)
        session.close()

    assert result["threshold_hours"] == 72
    assert result["count"] == 2
    assert {server["server_id"] for server in result["servers"]} == {
        "week-old",
        "ancient",
    }
    assert [bucket["bucket"] for bucket in result["buckets"]] == [
        "3-7d",
        ">30d",
    ]
    assert [bucket["bucket"] for bucket in buckets] == ["3-7d", ">30d"]
    assert sum(bucket["count"] for bucket in result["buckets"]) == result["count"]
    assert all(isinstance(server["age_hours"], float) for server in result["servers"])
    assert all(
        server["last_seen"].endswith("Z")
        and server["last_scanned"].endswith("Z")
        for server in result["servers"]
    )

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/staleness-watch",
            params={"threshold_hours": 72, "limit": 200},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert {server["server_id"] for server in payload["servers"]} == {
            "week-old",
            "ancient",
        }
        assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["count"]

    engine.dispose()


if __name__ == "__main__":
    _run_self_test()
    print("PASS")