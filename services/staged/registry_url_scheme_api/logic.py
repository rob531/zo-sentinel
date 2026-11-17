from collections import Counter, defaultdict
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


SCHEME_ORDER = ("http", "https", "stdio", "sse", "ws", "unix", "other", "empty")
MAX_SAMPLE_SERVERS = 5


def _build_url_scheme_stats(session: Session, limit: int) -> Dict[str, Any]:
    normalized_url = func.lower(
        func.trim(func.coalesce(McpServerRegistry.url, ""))
    )
    scheme = case(
        (normalized_url == "", "empty"),
        (normalized_url.like("https://%"), "https"),
        (normalized_url.like("http://%"), "http"),
        (normalized_url.like("stdio://%"), "stdio"),
        (normalized_url.like("sse://%"), "sse"),
        (normalized_url.like("ws://%"), "ws"),
        (normalized_url.like("unix://%"), "unix"),
        else_="other",
    )

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.url,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            scheme.label("scheme"),
        )
        .select_from(McpServerRegistry)
        .order_by(McpServerRegistry.server_id.asc())
        .limit(max(0, min(int(limit), 1000)))
    )
    rows = session.execute(statement).all()

    total = len(rows)
    bucket_counts = Counter(row.scheme for row in rows)
    tier_counts = Counter(
        str(row.risk_tier) if row.risk_tier is not None else "unknown"
        for row in rows
    )
    bucket_samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for row in rows:
        if len(bucket_samples[row.scheme]) >= MAX_SAMPLE_SERVERS:
            continue
        bucket_samples[row.scheme].append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
            }
        )

    buckets = [
        {
            "scheme": scheme_name,
            "count": bucket_counts[scheme_name],
            "pct": round((bucket_counts[scheme_name] / total * 100), 2)
            if total
            else 0.0,
            "sample_servers": bucket_samples.get(scheme_name, []),
        }
        for scheme_name in SCHEME_ORDER
        if bucket_counts[scheme_name] > 0
    ]

    by_tier = [
        {"risk_tier": tier, "count": count}
        for tier, count in sorted(tier_counts.items())
    ]

    return {
        "total": total,
        "buckets": buckets,
        "by_tier": by_tier,
    }


def get_url_scheme_stats(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> Dict[str, Any]:
    return _build_url_scheme_stats(session, limit)


def get_url_scheme_distribution(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> Dict[str, Any]:
    return _build_url_scheme_stats(session, limit)


def get_registry_url_scheme_stats(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> Dict[str, Any]:
    return _build_url_scheme_stats(session, limit)


def get_registry_url_scheme_distribution(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> Dict[str, Any]:
    return _build_url_scheme_stats(session, limit)


def get_registry_url_schemes(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> Dict[str, Any]:
    return _build_url_scheme_stats(session, limit)


def registry_url_scheme_stats(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> Dict[str, Any]:
    return _build_url_scheme_stats(session, limit)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="https-server",
                    url="https://example.test/mcp",
                    name="HTTPS Server",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="http-server",
                    url="http://example.test/mcp",
                    name="HTTP Server",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="stdio-server",
                    url="stdio://example.test/mcp",
                    name="Stdio Server",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="empty-server",
                    url=None,
                    name="Empty Server",
                    risk_tier="unknown",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/url-scheme")
    def read_url_scheme_stats(
        limit: int = 200,
        session: Session = Depends(get_session),
    ):
        return get_url_scheme_stats(session=session, limit=limit)

    response = TestClient(that_app).get(
        "/api/registry/url-scheme",
        params={"limit": 200},
    )
    assert response.status_code == 200
    payload = response.json()
    buckets = {bucket["scheme"]: bucket for bucket in payload["buckets"]}
    assert buckets["https"]["count"] == 1
    assert "empty" in buckets
    assert payload["total"] == 4
    print("PASS")