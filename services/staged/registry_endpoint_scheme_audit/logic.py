"""Logic for auditing registry endpoint transport schemes."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

SCHEME_ORDER = ("https", "http", "wss", "ws", "stdio", "unknown")
SECURE_SCHEMES = frozenset({"https", "wss"})


def classify_scheme(url: str | None) -> str:
    """Classify a registry URL into a known transport-scheme bucket."""
    if url is None:
        return "unknown"

    value = str(url).strip()
    if not value:
        return "unknown"

    if value.lower() == "stdio":
        return "stdio"

    scheme, separator, _ = value.partition(":")
    if not separator:
        return "unknown"

    normalized = scheme.strip().lower()
    if normalized in {"https", "http", "wss", "ws", "stdio"}:
        return normalized
    return "unknown"


def get_scheme_audit(db: Session) -> dict[str, Any]:
    """Summarize registry URL schemes and identify insecure endpoints."""
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.url,
            McpServerRegistry.risk_tier,
        ).order_by(McpServerRegistry.server_id)
    ).all()

    counts = {scheme: 0 for scheme in SCHEME_ORDER}
    high_risk_counts = {scheme: 0 for scheme in SCHEME_ORDER}
    insecure: list[dict[str, Any]] = []

    for server_id, url, risk_tier in rows:
        scheme = classify_scheme(url)
        counts[scheme] += 1

        if isinstance(risk_tier, str) and risk_tier.strip().lower() == "high":
            high_risk_counts[scheme] += 1

        if scheme not in SECURE_SCHEMES:
            insecure.append(
                {
                    "server_id": server_id,
                    "url": url,
                    "risk_tier": risk_tier,
                }
            )

    return {
        "total_servers": len(rows),
        "buckets": [
            {
                "scheme": scheme,
                "count": counts[scheme],
                "high_risk_count": high_risk_counts[scheme],
            }
            for scheme in SCHEME_ORDER
        ],
        "insecure": insecure,
    }


def audit_registry_schemes(db: Session) -> dict[str, Any]:
    """Return the registry endpoint scheme audit."""
    return get_scheme_audit(db)


def scheme_audit(db: Session) -> dict[str, Any]:
    """Return the registry endpoint scheme audit."""
    return get_scheme_audit(db)


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
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="https-server",
                    url="https://secure.example/mcp",
                    name="Secure server",
                    registry_source="test",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="http-server",
                    url="http://insecure.example/mcp",
                    name="Insecure server",
                    registry_source="test",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="wss-server",
                    url="wss://secure.example/socket",
                    name="Secure socket",
                    registry_source="test",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="stdio-server",
                    url="stdio://local-command",
                    name="Local process",
                    registry_source="test",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="unknown-server",
                    url=None,
                    name="Unknown endpoint",
                    registry_source="test",
                    risk_tier="medium",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_session() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/registry/endpoints/scheme-audit")
    def scheme_audit_endpoint(db: Session = Depends(get_session)) -> dict[str, Any]:
        result = get_scheme_audit(db)
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        return result

    with TestClient(test_app) as client:
        response = client.get("/api/registry/endpoints/scheme-audit")
        response.raise_for_status()
        payload = response.json()

        assert payload["total_servers"] == 5
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 5
        http_buckets = [
            bucket for bucket in payload["buckets"] if bucket["scheme"] == "http"
        ]
        assert len(http_buckets) == 1
        assert http_buckets[0]["count"] == 1
        assert "http-server" in {
            item["server_id"] for item in payload["insecure"]
        }

    print("PASS")