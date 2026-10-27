from collections import defaultdict
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _normalise_host(url):
    if not isinstance(url, str) or not url.strip():
        return "unknown"

    candidate = url.strip()
    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname

        if hostname is None and "://" not in candidate:
            parsed = urlparse(f"//{candidate}")
            hostname = parsed.hostname

        if not hostname:
            return "unknown"

        return hostname.lower()
    except (TypeError, ValueError):
        return "unknown"


def get_registry_url_host_rollup(
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    if limit < 1:
        raise HTTPException(status_code=422, detail="limit must be at least 1")

    rows = session.execute(
        select(
            McpServerRegistry.url,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        )
    ).all()

    grouped = defaultdict(
        lambda: {
            "servers": 0,
            "sources": set(),
            "tiers": set(),
            "trust_scores": [],
        }
    )

    for url, registry_source, risk_tier, trust_score in rows:
        host = _normalise_host(url)
        group = grouped[host]
        group["servers"] += 1

        if registry_source is not None:
            group["sources"].add(registry_source)
        if risk_tier is not None:
            group["tiers"].add(risk_tier)
        if trust_score is not None:
            group["trust_scores"].append(float(trust_score))

    ordered_hosts = sorted(
        grouped.items(),
        key=lambda item: (-item[1]["servers"], item[0]),
    )

    hosts = []
    for host, group in ordered_hosts[:limit]:
        trust_scores = group["trust_scores"]
        mean_trust_score = (
            round(sum(trust_scores) / len(trust_scores), 3)
            if trust_scores
            else None
        )
        hosts.append(
            {
                "host": host,
                "servers": group["servers"],
                "sources": len(group["sources"]),
                "tiers": len(group["tiers"]),
                "mean_trust_score": mean_trust_score,
            }
        )

    return {
        "total_servers": len(rows),
        "hosts": hosts,
        "host_count": len(hosts),
    }


def _run_self_test():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    McpServerRegistry.__table__.create(engine)

    with test_session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="host-a-1",
                    name="Host A 1",
                    url="https://REGISTRY.EXAMPLE.COM:8443/tools/one",
                    registry_source="github",
                    risk_tier="medium",
                    trust_score=0.8,
                ),
                McpServerRegistry(
                    server_id="host-a-2",
                    name="Host A 2",
                    url="http://registry.example.com/tools/two",
                    registry_source="github",
                    risk_tier="high",
                    trust_score=0.9,
                ),
                McpServerRegistry(
                    server_id="host-a-3",
                    name="Host A 3",
                    url="https://registry.example.com/tools/three",
                    registry_source="internal",
                    risk_tier="high",
                    trust_score=1.0,
                ),
                McpServerRegistry(
                    server_id="host-b-1",
                    name="Host B 1",
                    url="https://other.example.com/tools/four",
                    registry_source="github",
                    risk_tier="low",
                    trust_score=0.5,
                ),
                McpServerRegistry(
                    server_id="host-unknown-1",
                    name="Host Unknown 1",
                    url=None,
                    registry_source="manual",
                    risk_tier="medium",
                    trust_score=0.2,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/registry/url-host-rollup")
    def url_host_rollup(limit: int = 10, session: Session = Depends(get_session)):
        return get_registry_url_host_rollup(limit=limit, session=session)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/url-host-rollup",
            params={"limit": 10},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 5
        assert payload["hosts"][0]["host"] == "registry.example.com"
        assert payload["hosts"][0]["servers"] == 3
        assert payload["hosts"][0]["sources"] == 2
        assert payload["hosts"][0]["tiers"] == 2
        assert payload["hosts"][0]["mean_trust_score"] == 0.9
        assert any(
            host["host"] == "unknown" and host["servers"] == 1
            for host in payload["hosts"]
        )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()