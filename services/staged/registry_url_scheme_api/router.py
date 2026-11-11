from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_url_scheme_stats as _get_registry_url_scheme_stats
except ImportError:
    try:
        from .logic import get_registry_url_scheme as _get_registry_url_scheme_stats
    except ImportError:
        try:
            from .logic import get_url_scheme_stats as _get_registry_url_scheme_stats
        except ImportError:
            try:
                from .logic import get_url_scheme_distribution as _get_registry_url_scheme_stats
            except ImportError:
                _get_registry_url_scheme_stats = None


router = APIRouter(prefix="/api", tags=["registry"])


class RegistryUrlSchemeSample(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None


class RegistryUrlSchemeBucket(BaseModel):
    scheme: str
    count: int
    pct: float
    sample_servers: List[RegistryUrlSchemeSample] = []


class RegistryUrlSchemeTier(BaseModel):
    risk_tier: str
    count: int


class RegistryUrlSchemeResponse(BaseModel):
    total: int
    buckets: List[RegistryUrlSchemeBucket]
    by_tier: List[RegistryUrlSchemeTier]


def _read_registry_url_schemes(session: Session, limit: int) -> Dict[str, Any]:
    from sqlalchemy import text

    rows = session.execute(
        text(
            """
            SELECT
                server_id,
                url,
                name,
                risk_tier,
                CASE
                    WHEN LOWER(TRIM(COALESCE(url, ''))) = '' THEN 'empty'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'https://%' THEN 'https'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'http://%' THEN 'http'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'stdio://%' THEN 'stdio'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'sse://%' THEN 'sse'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'ws://%' THEN 'ws'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'wss://%' THEN 'ws'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'unix://%' THEN 'unix'
                    WHEN LOWER(TRIM(COALESCE(url, ''))) LIKE 'unix:%' THEN 'unix'
                    ELSE 'other'
                END AS scheme
            FROM mcp_server_registry
            ORDER BY server_id
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings()

    records = [dict(row) for row in rows]
    total = len(records)
    scheme_order = ("http", "https", "stdio", "sse", "ws", "unix", "other", "empty")
    counts = {scheme: 0 for scheme in scheme_order}
    samples: Dict[str, List[Dict[str, Any]]] = {scheme: [] for scheme in scheme_order}
    tier_counts: Dict[str, int] = {}

    for record in records:
        scheme = record.get("scheme") or "other"
        counts[scheme] = counts.get(scheme, 0) + 1
        if len(samples.setdefault(scheme, [])) < 5:
            samples[scheme].append(
                {
                    "server_id": record.get("server_id"),
                    "name": record.get("name"),
                    "risk_tier": record.get("risk_tier"),
                }
            )

        tier = record.get("risk_tier") or "unknown"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    buckets = []
    for scheme in scheme_order:
        if counts.get(scheme, 0) == 0:
            continue
        count = counts[scheme]
        buckets.append(
            {
                "scheme": scheme,
                "count": count,
                "pct": round((count * 100.0 / total), 2) if total else 0.0,
                "sample_servers": samples.get(scheme, []),
            }
        )

    return {
        "total": total,
        "buckets": buckets,
        "by_tier": [
            {"risk_tier": tier, "count": tier_counts[tier]}
            for tier in sorted(tier_counts)
        ],
    }


@router.get(
    "/registry/url-scheme",
    response_model=RegistryUrlSchemeResponse,
    summary="Registry URL scheme distribution",
)
def registry_url_scheme(
    limit: int = 200,
    session: Session = Depends(get_session),
) -> RegistryUrlSchemeResponse:
    bounded_limit = max(1, min(int(limit), 1000))
    if _get_registry_url_scheme_stats is not None:
        payload = _get_registry_url_scheme_stats(session, bounded_limit)
    else:
        payload = _read_registry_url_schemes(session, bounded_limit)
    return RegistryUrlSchemeResponse.parse_obj(payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    assert McpServerRegistry.__tablename__ == "mcp_server_registry"

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    url TEXT,
                    name VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, url, name, risk_tier)
                VALUES
                    (:server_id, :url, :name, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "srv-https",
                    "url": "https://example.test/mcp",
                    "name": "HTTPS Server",
                    "risk_tier": "low",
                },
                {
                    "server_id": "srv-http",
                    "url": "http://example.test/mcp",
                    "name": "HTTP Server",
                    "risk_tier": "medium",
                },
                {
                    "server_id": "srv-stdio",
                    "url": "stdio:///usr/local/bin/server",
                    "name": "Stdio Server",
                    "risk_tier": "high",
                },
                {
                    "server_id": "srv-empty",
                    "url": "",
                    "name": "Empty URL Server",
                    "risk_tier": "unknown",
                },
            ],
        )

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/url-scheme", params={"limit": 200})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4, payload
        buckets = {bucket["scheme"]: bucket for bucket in payload["buckets"]}
        assert buckets["https"]["count"] == 1, payload
        assert "empty" in buckets, payload

    print("PASS")