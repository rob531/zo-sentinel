from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class HostConcentrationEntry(BaseModel):
    host: str
    server_count: int
    verdicts: dict[str, int]
    distinct_verdicts: int
    risk_tiers: list[str]


class HostConcentrationResponse(BaseModel):
    total_servers: int
    distinct_hosts: int
    hosts: list[HostConcentrationEntry]


RegistryHostConcentrationResponse = HostConcentrationResponse
HostConcentrationHost = HostConcentrationEntry


def extract_hostname(url: str | None) -> str | None:
    """Extract and normalize a URL hostname using only the standard library."""
    if not url:
        return None

    candidate = url.strip()
    if not candidate:
        return None

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        if hostname is None and "://" not in candidate:
            parsed = urlsplit(f"//{candidate}")
            hostname = parsed.hostname
    except ValueError:
        return None

    if not hostname:
        return None

    normalized = hostname.rstrip(".").lower()
    return normalized or None


def _load_registry_rows(session: Session) -> list[Any]:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.url,
        McpServerRegistry.registry_source,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
    )
    return session.execute(statement).mappings().all()


def get_host_concentration(
    session: Session = Depends(get_session),
    limit: int = 100,
) -> dict[str, Any]:
    if limit < 0:
        raise ValueError("limit must be non-negative")

    rows = _load_registry_rows(session)
    host_rows: dict[str, list[Any]] = defaultdict(list)

    for row in rows:
        host = extract_hostname(row["url"])
        if host is not None:
            host_rows[host].append(row)

    hosts: list[dict[str, Any]] = []
    for host, entries in host_rows.items():
        verdict_counts = Counter(
            row["verdict"] for row in entries if row["verdict"] is not None
        )
        risk_tiers = sorted(
            {row["risk_tier"] for row in entries if row["risk_tier"] is not None}
        )
        hosts.append(
            {
                "host": host,
                "server_count": len(entries),
                "verdicts": dict(sorted(verdict_counts.items())),
                "distinct_verdicts": len(verdict_counts),
                "risk_tiers": risk_tiers,
            }
        )

    hosts.sort(key=lambda item: (-item["server_count"], item["host"]))

    return {
        "total_servers": len(rows),
        "distinct_hosts": len(host_rows),
        "hosts": hosts[:limit],
    }


get_registry_host_concentration = get_host_concentration
compute_host_concentration = get_host_concentration


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    url="https://shared.example/path",
                    registry_source="test",
                    verdict="benign",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    url="https://shared.example:443/other",
                    registry_source="test",
                    verdict="suspicious",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    url="https://second.example/path",
                    registry_source="test",
                    verdict="benign",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="server-4",
                    name="Server Four",
                    url="https://third.example/path",
                    registry_source="test",
                    verdict="benign",
                    risk_tier="high",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app = FastAPI()

    @test_app.get(
        "/api/registry/host-concentration",
        response_model=HostConcentrationResponse,
    )
    def host_concentration(session: Session = Depends(get_session)):
        return get_host_concentration(session)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/host-concentration")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["distinct_hosts"] == 3
        shared = next(
            item for item in payload["hosts"] if item["host"] == "shared.example"
        )
        assert shared["server_count"] == 2
        assert shared["distinct_verdicts"] == 2

    print("PASS")