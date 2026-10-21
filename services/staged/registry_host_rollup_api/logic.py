from __future__ import annotations

from collections import defaultdict
from math import isfinite
from urllib.parse import urlparse

from app.db import get_session
from app.models import McpServerRegistry


TIER_KEYS = (
    "HIGH_RISK_ISOLATED",
    "CAUTION_LIMITED",
    "ENTERPRISE_CONTROLLED",
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)


def _extract_host(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""

    value = url.strip()
    try:
        parsed = urlparse(value if "://" in value else f"//{value}")
        host = parsed.hostname
    except ValueError:
        return ""

    return host.strip().lower() if host else ""


def _normalize_trust_score(value: object) -> float:
    if value is None:
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return score if isfinite(score) else 0.0


def host_rollup(db_session, limit: int = 50) -> dict:
    rows = db_session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.url,
        McpServerRegistry.registry_source,
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
    ).all()

    grouped: dict[str, dict] = defaultdict(
        lambda: {
            "server_count": 0,
            "tiers": {tier: 0 for tier in TIER_KEYS},
            "trust_total": 0.0,
        }
    )

    for row in rows:
        host = _extract_host(row.url)
        trust_score = _normalize_trust_score(row.trust_score)
        group = grouped[host]
        group["server_count"] += 1
        group["trust_total"] += trust_score

        risk_tier = row.risk_tier
        if risk_tier is not None:
            normalized_tier = str(risk_tier).strip().upper()
            if normalized_tier in group["tiers"]:
                group["tiers"][normalized_tier] += 1

    servers = [
        {
            "host": host,
            "server_count": values["server_count"],
            "tiers": values["tiers"],
            "avg_trust_score": values["trust_total"] / values["server_count"],
        }
        for host, values in grouped.items()
    ]
    servers.sort(key=lambda item: (-item["server_count"], item["host"]))

    bounded_limit = min(max(int(limit), 0), 200)
    return {
        "total_hosts": len(grouped),
        "servers": servers[:bounded_limit],
    }


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    url TEXT,
                    registry_source TEXT,
                    trust_score REAL,
                    risk_tier TEXT,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, url, registry_source, trust_score, risk_tier, verdict)
                VALUES
                    ('one', 'One', 'https://alpha.example/api', 'official', 90, 'TRUSTED_GENERAL', 'ok'),
                    ('two', 'Two', 'https://alpha.example/mcp', 'official', 80, 'TRUSTED_GENERAL', 'ok'),
                    ('three', 'Three', 'https://alpha.example/tools', 'official', 70, 'CAUTION_LIMITED', 'review'),
                    ('four', 'Four', 'https://beta.example/mcp', 'community', 40, 'CAUTION_LIMITED', 'review')
                """
            )
        )

    testing_session = sessionmaker(bind=engine)()
    that_app = FastAPI()

    def override_session():
        yield testing_session

    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/registry/hosts")
    def registry_hosts(limit: int = 50, db_session=Depends(get_session)):
        return host_rollup(db_session, limit)

    response = TestClient(that_app).get("/api/registry/hosts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_hosts"] == 2
    assert payload["servers"][0]["host"] == "alpha.example"
    assert payload["servers"][0]["server_count"] == 3
    assert all(tier in payload["servers"][0]["tiers"] for tier in TIER_KEYS)
    print("PASS")


if __name__ == "__main__":
    _self_test()