from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink
    from .logic import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(engine)
        TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

        with TestingSession() as session:
            servers = [
                McpServerRegistry(server_id="srv-a1", name="family-a.server-1"),
                McpServerRegistry(server_id="srv-a2", name="family-a.server-2"),
                McpServerRegistry(server_id="srv-b1", name="family-b.server-1"),
                McpServerRegistry(server_id="srv-b2", name="family-b.server-2"),
                McpServerRegistry(server_id="srv-c1", name="family-c.server-1"),
            ]
            advisory = VulnAdvisory(
                id="CVE-2025-0001",
                feed="test",
                summary="Contract self-test advisory",
                source_url="https://example.invalid/CVE-2025-0001",
            )
            session.add_all([*servers, advisory])
            session.flush()
            session.add_all(
                [
                    VulnLink(
                        advisory_id=advisory.id,
                        server_id=server_id,
                        match_basis="repo_exact",
                        match_value=server_id,
                        match_confidence=1.0,
                    )
                    for server_id in ("srv-a1", "srv-b1", "srv-c1")
                ]
            )
            session.commit()

        def override_get_session():
            with TestingSession() as session:
                yield session

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/families/coverage")

        assert response.status_code == 200, response.text
        body = response.json()
        families = body.get("families")
        assert isinstance(families, list), body
        assert len(families) == 3, body

        by_family = {family["family_id"]: family for family in families}
        assert set(by_family) == {"family-a", "family-b", "family-c"}, body
        assert sum(family["server_count"] for family in families) == 5, body
        assert sum(family["cve_affected"] for family in families) == 3, body
        assert sum(family["cve_unknown"] for family in families) == 2, body
        assert all(family["coverage_pct"] > 0 for family in families), body
        assert by_family["family-a"]["coverage_pct"] == 50.0, body
        assert by_family["family-b"]["coverage_pct"] == 50.0, body
        assert by_family["family-c"]["coverage_pct"] == 100.0, body
        return True
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)