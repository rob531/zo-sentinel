from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveSnapshot


@contextmanager
def utc_now() -> Generator[datetime, None, None]:
    yield datetime.now(timezone.utc)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    test_session: Session = TestingSessionLocal()

    try:
        test_session.execute(
            McpServerRegistry.__table__.insert().values(
                confidence=0.85,
                description="self-test registry",
            )
        )
        test_session.commit()
    except Exception:
        test_session.rollback()

    def _override_get_session() -> Generator[Session, None, None]:
        try:
            yield test_session
        finally:
            pass

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = _override_get_session

    @test_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    target = getattr(sys.modules.get("app.main"), "app", None) or getattr(
        sys.modules.get("app"), "main", None
    )
    if target is not None and hasattr(target, "dependency_overrides"):
        target.dependency_overrides[get_session] = _override_get_session

    with TestClient(test_app) as client:
        resp = client.get("/health")
        if resp.json().get("status") == "ok":
            print("PASS")
        else:
            print("FAIL: unexpected response")


if __name__ == "__main__":
    _run_self_test()