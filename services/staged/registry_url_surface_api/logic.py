from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_SCHEME_ORDER = ("http", "https", "npx", "pip", "other", "empty")
_MAX_HOST_LIMIT = 1000


def _normalise_limit(limit: int | None) -> int:
    if limit is None:
        return 10
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 10
    return max(0, min(value, _MAX_HOST_LIMIT))


def _parse_url(value: str | None) -> tuple[str, str]:
    if value is None or not value.strip():
        return "empty", ""

    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return "other", ""

    scheme = (parsed.scheme or "").lower()
    if not scheme:
        return "other", ""

    if scheme in {"http", "https"}:
        host = (parsed.netloc or "").lower()
    else:
        host = ""

    if scheme not in {"http", "https", "npx", "pip"}:
        scheme = "other"

    return scheme, host


def get_url_surface(
    db: Session | None = None,
    limit: int | None = None,
    *,
    session: Session | None = None,
) -> dict:
    """Build the URL surface for entries in the server registry."""
    if db is None:
        db = session
    if db is None:
        raise ValueError("A database session is required")

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
        )
    ).all()

    scheme_counts = Counter({scheme: 0 for scheme in _SCHEME_ORDER})
    host_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    missing_url = 0

    for row in rows:
        raw_url = row.url
        if raw_url is None or not str(raw_url).strip():
            missing_url += 1

        scheme, host = _parse_url(raw_url)
        scheme_counts[scheme] += 1
        if host:
            host_counts[host] += 1

        source = row.registry_source or ""
        source_counts[source] += 1

    by_scheme = [
        {"scheme": scheme, "count": scheme_counts[scheme]}
        for scheme in _SCHEME_ORDER
    ]

    by_host = [
        {"host": host, "count": count}
        for host, count in sorted(
            host_counts.items(), key=lambda item: (-item[1], item[0])
        )[: _normalise_limit(limit)]
    ]

    by_registry_source = [
        {"registry_source": source, "count": count}
        for source, count in sorted(
            source_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    return {
        "server_count": len(rows),
        "missing_url": missing_url,
        "by_scheme": by_scheme,
        "by_host": by_host,
        "by_registry_source": by_registry_source,
    }


def get_registry_url_surface(
    db: Session | None = None,
    limit: int | None = None,
    *,
    session: Session | None = None,
) -> dict:
    return get_url_surface(db=db, limit=limit, session=session)


def build_url_surface(
    db: Session | None = None,
    limit: int | None = None,
    *,
    session: Session | None = None,
) -> dict:
    return get_url_surface(db=db, limit=limit, session=session)


def _self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc)
    testing_session.execute(
        insert(McpServerRegistry),
        [
            {
                "server_id": "one",
                "name": "one",
                "registry_source": "npm",
                "url": "https://a.example/x",
                "trust_score": 0.5,
                "verdict": "ok",
                "risk_tier": "low",
                "last_seen": now,
                "scan_count": 1,
            },
            {
                "server_id": "two",
                "name": "two",
                "registry_source": "npm",
                "url": "https://a.example/y",
                "trust_score": 0.5,
                "verdict": "ok",
                "risk_tier": "low",
                "last_seen": now,
                "scan_count": 1,
            },
            {
                "server_id": "three",
                "name": "three",
                "registry_source": "pypi",
                "url": "http://b.example",
                "trust_score": 0.5,
                "verdict": "ok",
                "risk_tier": "low",
                "last_seen": now,
                "scan_count": 1,
            },
            {
                "server_id": "four",
                "name": "four",
                "registry_source": "other",
                "url": None,
                "trust_score": 0.5,
                "verdict": "ok",
                "risk_tier": "low",
                "last_seen": now,
                "scan_count": 1,
            },
        ],
    )
    testing_session.commit()

    def override_get_session():
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/url-surface")
    def url_surface(limit: int = 10, db: Session = Depends(get_session)):
        return get_url_surface(db=db, limit=limit)

    response = TestClient(that_app).get("/api/registry/url-surface?limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert any(
        item["scheme"] == "https" and item["count"] == 2
        for item in payload["by_scheme"]
    )
    assert payload["missing_url"] == 1
    assert payload["by_host"][0] == {"host": "a.example", "count": 2}

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()