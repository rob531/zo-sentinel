from __future__ import annotations

import sys
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from . import router as router_module
from .logic import get_link_confidence_buckets as get_link_confidence_buckets_logic
from .router import router


def run_self_test() -> bool:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    VulnAdvisory.__table__.create(bind=engine)
    VulnLink.__table__.create(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    confidences = (0.1, 0.3, 0.5, 0.7, 0.9, 0.95)
    with session_factory() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=f"ADV-{index}",
                    feed="contract",
                    source_url=f"https://example.test/advisories/{index}",
                    severity=severity,
                    ecosystem=ecosystem,
                )
                for index, (severity, ecosystem) in enumerate(
                    (("high", "npm"), ("medium", "pypi"), ("low", "go")),
                    start=1,
                )
            ]
        )
        session.flush()
        session.add_all(
            [
                VulnLink(
                    id=index,
                    advisory_id=f"ADV-{((index - 1) % 3) + 1}",
                    server_id=f"contract-server-{index}",
                    match_basis="exact" if index % 2 else "package",
                    match_value=f"package-{index}",
                    match_confidence=confidence,
                    linked_at=now,
                )
                for index, confidence in enumerate(confidences, start=1)
            ]
        )
        session.commit()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    original_logic_binding = router_module.get_link_confidence_buckets

    def invoke_logic_with_route_arguments(session: Session, days: int):
        return get_link_confidence_buckets_logic(days=days, session=session)

    router_module.get_link_confidence_buckets = invoke_logic_with_route_arguments
    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/vuln/link-confidence",
                params={"days": 90},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_links"] == 6, payload
        high_confidence_band = next(
            bucket for bucket in payload["buckets"]
            if bucket["band"] == "0.8-1.0"
        )
        assert high_confidence_band["count"] == 2, payload
        assert sum(payload["by_match_basis"].values()) == payload["total_links"], payload
    finally:
        router_module.get_link_confidence_buckets = original_logic_binding
        test_app.dependency_overrides.clear()
        engine.dispose()

    return True


def run() -> bool:
    return run_self_test()


_run_self_test = run_self_test
_self_test = run_self_test
run_contract_test = run_self_test


def main() -> None:
    try:
        run_self_test()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()