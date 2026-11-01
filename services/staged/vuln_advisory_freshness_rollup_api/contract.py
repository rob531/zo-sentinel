from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from services.staged.vuln_advisory_freshness_rollup_api.router import router as service_router


def _build_test_app() -> tuple[FastAPI, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    stale_at = now - timedelta(hours=48)
    recent_at = now - timedelta(hours=1)

    with test_session_factory() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="alpha",
                    summary="Advisory one",
                    severity="CRITICAL",
                    ecosystem="npm",
                    package="example-one",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://advisories.example/1",
                    published_at=now - timedelta(days=4),
                    fetched_at=stale_at,
                    content_hash="a" * 64,
                ),
                VulnAdvisory(
                    id=2,
                    feed="alpha",
                    summary="Advisory two",
                    severity="HIGH",
                    ecosystem="npm",
                    package="example-two",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://advisories.example/2",
                    published_at=now - timedelta(days=3),
                    fetched_at=recent_at,
                    content_hash="b" * 64,
                ),
                VulnAdvisory(
                    id=3,
                    feed="alpha",
                    summary="Advisory three",
                    severity="MEDIUM",
                    ecosystem="pypi",
                    package="example-three",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://advisories.example/3",
                    published_at=now - timedelta(days=2),
                    fetched_at=recent_at,
                    content_hash="c" * 64,
                ),
                VulnAdvisory(
                    id=4,
                    feed="beta",
                    summary="Advisory four",
                    severity="HIGH",
                    ecosystem="npm",
                    package="example-four",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://advisories.example/4",
                    published_at=now - timedelta(days=1),
                    fetched_at=recent_at,
                    content_hash="d" * 64,
                ),
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=101,
                    match_basis="package",
                    match_confidence=0.99,
                    match_value="example-one",
                    linked_at=now - timedelta(hours=2),
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=102,
                    match_basis="package",
                    match_confidence=0.95,
                    match_value="example-two",
                    linked_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    route_paths = {getattr(route, "path", "") for route in service_router.routes}
    if "/api/vuln/freshness" in route_paths:
        prefix = ""
    elif "/vuln/freshness" in route_paths:
        prefix = "/api"
    else:
        prefix = "/api/vuln"
    test_app.include_router(service_router, prefix=prefix)
    test_app.dependency_overrides[get_session] = override_get_session
    return test_app, engine


def run_contract_test() -> None:
    test_app, engine = _build_test_app()
    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/freshness")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["total_advisories"] == 4
            assert payload["total_links"] == 2

            alpha = next(feed for feed in payload["feeds"] if feed["feed"] == "alpha")
            assert alpha["total"] == 3
            assert alpha["by_severity"] == {
                "CRITICAL": 1,
                "HIGH": 1,
                "MEDIUM": 1,
            }
            assert alpha["linked_count"] == 2
            assert alpha["stale_over_24h"] == 1
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_contract_test()