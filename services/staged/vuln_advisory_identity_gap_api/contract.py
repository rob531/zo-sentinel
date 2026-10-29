from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .router import router


def _seed_database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    Session = sessionmaker(bind=engine)
    db = Session()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    advisories = [
        VulnAdvisory(
            id=1,
            feed="osv",
            summary="Advisory one",
            severity="high",
            ecosystem="PyPI",
            package="example-one",
            affected_ranges=[],
            aliases=["GHSA-example-one"],
            source_url="https://example.test/1",
            published_at=now,
            fetched_at=now,
            identities=["CVE-2024-0001"],
            content_hash="hash-1",
        ),
        VulnAdvisory(
            id=2,
            feed="osv",
            summary="Advisory two",
            severity="medium",
            ecosystem="npm",
            package="example-two",
            affected_ranges=[],
            aliases=["GHSA-example-two"],
            source_url="https://example.test/2",
            published_at=now,
            fetched_at=now,
            identities=[],
            content_hash="hash-2",
        ),
        VulnAdvisory(
            id=3,
            feed="github",
            summary="Advisory three",
            severity="critical",
            ecosystem="Go",
            package="example-three",
            affected_ranges=[],
            aliases=[],
            source_url="https://example.test/3",
            published_at=now,
            fetched_at=now,
            identities=["CVE-2024-0003"],
            content_hash="hash-3",
        ),
        VulnAdvisory(
            id=4,
            feed="github",
            summary="Advisory four",
            severity="low",
            ecosystem="Rust",
            package="example-four",
            affected_ranges=[],
            aliases=["GHSA-example-four"],
            source_url="https://example.test/4",
            published_at=now,
            fetched_at=now,
            identities=["CVE-2024-0004"],
            content_hash="hash-4",
        ),
    ]

    links = [
        VulnLink(
            id=1,
            advisory_id=1,
            server_id=101,
            match_basis="identity",
            match_value="CVE-2024-0001",
            match_confidence=1.0,
            linked_at=now,
        ),
        VulnLink(
            id=2,
            advisory_id=2,
            server_id=102,
            match_basis="identity",
            match_value="CVE-2024-0002",
            match_confidence=1.0,
            linked_at=now,
        ),
        VulnLink(
            id=3,
            advisory_id=3,
            server_id=103,
            match_basis="identity",
            match_value="CVE-2024-0003",
            match_confidence=1.0,
            linked_at=now,
        ),
    ]

    db.add_all(advisories)
    db.add_all(links)
    db.commit()
    return db, engine


def test_contract():
    db, engine = _seed_database()

    def override_get_session():
        yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/identity-gap")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total_advisories"] == 4
            assert payload["advisories_without_identities"] == 1
            assert payload["advisories_without_aliases"] == 1
            assert payload["unlinked_advisories"] == 1
            assert isinstance(payload["by_feed"], list)
            assert payload["by_feed"]
            assert isinstance(payload["by_severity"], list)
            assert payload["by_severity"]
    finally:
        db.close()
        engine.dispose()

    print("PASS")


def run():
    test_contract()


if __name__ == "__main__":
    run()