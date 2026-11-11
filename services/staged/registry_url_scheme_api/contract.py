from __future__ import annotations

from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class RegistryServerSample(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None


class UrlSchemeBucket(BaseModel):
    scheme: str
    count: int
    pct: float
    sample_servers: List[RegistryServerSample] = Field(default_factory=list)


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int


class RegistryUrlSchemeResponse(BaseModel):
    total: int
    buckets: List[UrlSchemeBucket]
    by_tier: List[RiskTierCount]


def classify_url_scheme(url: Optional[str]) -> str:
    value = (url or "").strip().lower()
    if not value:
        return "empty"
    if value.startswith("https://"):
        return "https"
    if value.startswith("http://"):
        return "http"
    if value.startswith("stdio://"):
        return "stdio"
    if value.startswith("sse://"):
        return "sse"
    if value.startswith("ws://") or value.startswith("wss://"):
        return "ws"
    if value.startswith("unix://"):
        return "unix"
    return "other"


def get_registry_url_scheme(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> RegistryUrlSchemeResponse:
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.url,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .order_by(McpServerRegistry.server_id)
        .limit(limit)
    ).all()

    buckets: dict[str, dict] = {}
    tier_counts: dict[str, int] = {}

    for server_id, url, name, risk_tier in rows:
        scheme = classify_url_scheme(url)
        bucket = buckets.setdefault(
            scheme,
            {"count": 0, "sample_servers": []},
        )
        bucket["count"] += 1
        if len(bucket["sample_servers"]) < 3:
            bucket["sample_servers"].append(
                {
                    "server_id": server_id,
                    "name": name,
                    "risk_tier": risk_tier,
                }
            )

        tier = risk_tier or "unknown"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    total = len(rows)
    bucket_models = []
    for scheme in ("http", "https", "stdio", "sse", "ws", "unix", "other", "empty"):
        bucket = buckets.get(scheme)
        if bucket is None:
            continue
        count = bucket["count"]
        bucket_models.append(
            UrlSchemeBucket(
                scheme=scheme,
                count=count,
                pct=round((count / total) * 100, 2) if total else 0.0,
                sample_servers=bucket["sample_servers"],
            )
        )

    by_tier = [
        RiskTierCount(risk_tier=tier, count=count)
        for tier, count in sorted(tier_counts.items())
    ]

    return RegistryUrlSchemeResponse(
        total=total,
        buckets=bucket_models,
        by_tier=by_tier,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/registry/url-scheme",
    response_model=RegistryUrlSchemeResponse,
)
def registry_url_scheme(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> RegistryUrlSchemeResponse:
    return get_registry_url_scheme(limit=limit, db=db)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    def override_get_session() -> Iterator[Session]:
        with Session(test_engine) as session:
            yield session

    seeded_rows = [
        {
            "server_id": "srv-https",
            "url": "https://example.test/secure",
            "name": "HTTPS Server",
            "risk_tier": "low",
        },
        {
            "server_id": "srv-http",
            "url": "http://example.test/insecure",
            "name": "HTTP Server",
            "risk_tier": "medium",
        },
        {
            "server_id": "srv-stdio",
            "url": "stdio://example.test/server",
            "name": "Stdio Server",
            "risk_tier": "low",
        },
        {
            "server_id": "srv-empty",
            "url": "",
            "name": "Empty URL Server",
            "risk_tier": "unknown",
        },
    ]

    with Session(test_engine) as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, url, name, risk_tier)
                VALUES
                    (:server_id, :url, :name, :risk_tier)
                """
            ),
            seeded_rows,
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/url-scheme?limit=200")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert any(
            bucket["scheme"] == "https" and bucket["count"] == 1
            for bucket in payload["buckets"]
        )
        assert any(
            bucket["scheme"] == "empty"
            for bucket in payload["buckets"]
        )

    print("PASS")