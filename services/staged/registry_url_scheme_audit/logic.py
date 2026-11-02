"""Registry URL scheme audit data and computation layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


SCHEME_BUCKETS = ("https", "http", "wss", "ws", "other/none")


def _classify_url(url: Optional[str]) -> str:
    if not url:
        return "other/none"
    try:
        scheme = urlsplit(url.strip()).scheme.lower()
    except ValueError:
        return "other/none"
    return scheme if scheme in SCHEME_BUCKETS else "other/none"


def _is_insecure_url(url: Optional[str]) -> bool:
    scheme = _classify_url(url)
    if scheme not in {"http", "ws"}:
        return False
    try:
        hostname = (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        hostname = ""
    return hostname != "localhost"


def url_scheme_audit(
    db: Session = Depends(get_session),
    limit: int = 1000,
) -> Dict[str, object]:
    """Classify registry URLs by transport scheme using the real app database."""
    if limit < 0:
        raise ValueError("limit must be non-negative")

    rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.url)
        .order_by(McpServerRegistry.server_id)
        .limit(limit)
    ).all()

    by_scheme: Dict[str, int] = {scheme: 0 for scheme in SCHEME_BUCKETS}
    non_https_examples: List[Dict[str, object]] = []
    insecure_count = 0

    for server_id, name, url in rows:
        scheme = _classify_url(url)
        by_scheme[scheme] += 1

        if _is_insecure_url(url):
            insecure_count += 1
            non_https_examples.append(
                {"server_id": server_id, "name": name, "url": url}
            )

    return {
        "total": len(rows),
        "by_scheme": by_scheme,
        "non_https_examples": non_https_examples,
        "insecure_count": insecure_count,
        "scanned_at": datetime.now(timezone.utc),
    }


def audit_registry_url_schemes(
    db: Session = Depends(get_session),
    limit: int = 1000,
) -> Dict[str, object]:
    """Compatibility alias for the registry URL scheme audit."""
    return url_scheme_audit(db=db, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/url-scheme-audit")
    def audit_endpoint(
        limit: int = 100,
        db: Session = Depends(get_session),
    ):
        return url_scheme_audit(db=db, limit=limit)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="https-server",
                    name="HTTPS Server",
                    url="https://example.com/mcp",
                ),
                McpServerRegistry(
                    server_id="http-server",
                    name="HTTP Server",
                    url="http://example.com/mcp",
                ),
                McpServerRegistry(
                    server_id="wss-server",
                    name="WSS Server",
                    url="wss://example.com/mcp",
                ),
                McpServerRegistry(
                    server_id="ws-server",
                    name="WS Server",
                    url="ws://example.com/mcp",
                ),
                McpServerRegistry(
                    server_id="none-server",
                    name="No URL Server",
                    url=None,
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get(
        "/api/registry/url-scheme-audit",
        params={"limit": 100},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 5
    assert sum(payload["by_scheme"].values()) == 5
    assert payload["by_scheme"] == {
        "https": 1,
        "http": 1,
        "wss": 1,
        "ws": 1,
        "other/none": 1,
    }
    assert payload["insecure_count"] == 2
    assert len(payload["non_https_examples"]) == 2
    assert payload["scanned_at"]
    print("PASS")