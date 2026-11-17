from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import VulnAdvisory

    from .logic import detect_alias_collisions
    from .router import AliasCollisionResponse

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id="1",
                    feed="osv",
                    ecosystem="npm",
                    package="example-one",
                    aliases=["CVE-2024-0001"],
                    source_url="https://example.invalid/advisory/1",
                    content_hash="hash-1",
                    published_at=datetime.now(timezone.utc),
                ),
                VulnAdvisory(
                    id="2",
                    feed="ghsa",
                    ecosystem="npm",
                    package="example-two",
                    aliases={"primary": " CVE-2024-0001 "},
                    source_url="https://example.invalid/advisory/2",
                    content_hash="hash-2",
                    published_at=datetime.now(timezone.utc),
                ),
                VulnAdvisory(
                    id="3",
                    feed="osv",
                    ecosystem="npm",
                    package="example-three",
                    aliases=["CVE-2024-9999"],
                    source_url="https://example.invalid/advisory/3",
                    content_hash="hash-3",
                    published_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get(
        "/api/vuln/alias-collisions",
        response_model=AliasCollisionResponse,
    )
    def alias_collisions(
        ecosystem: str = Query(..., min_length=1),
        session: Session = Depends(get_session),
    ) -> AliasCollisionResponse:
        payload = detect_alias_collisions(ecosystem, session)
        return AliasCollisionResponse(**payload)

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/vuln/alias-collisions",
        params={"ecosystem": "npm"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["collision_count"] == 1, body
    assert len(body["collisions"]) == 1, body
    assert body["collisions"][0]["alias"] == "CVE-2024-0001", body
    assert len(body["collisions"][0]["advisory_ids"]) == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)