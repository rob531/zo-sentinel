from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import ApiKey, Base

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    s = TestingSession()
    s.add(ApiKey(
        id="key-age-5",
        org_id="org-ageing",
        key_hash="hash-age-5",
        label="five-day-key",
        created_at=now - timedelta(days=5),
    ))
    s.add(ApiKey(
        id="key-age-45",
        org_id="org-ageing",
        key_hash="hash-age-45",
        label="forty-five-day-key",
        created_at=now - timedelta(days=45),
    ))
    s.add(ApiKey(
        id="key-age-200",
        org_id="org-ageing",
        key_hash="hash-age-200",
        label="two-hundred-day-key",
        created_at=now - timedelta(days=200),
    ))
    s.commit()
    s.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/keys/ageing", params={"days": 30})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["threshold_days"] == 30, body
    assert body["total_keys"] == 3, body
    assert body["buckets"][0] == {"bucket": "0-30", "count": 1}, body

    stale_ids = {item["id"] for item in body["stale_keys"]}
    assert "key-age-200" in stale_ids, body
    assert "key-age-5" not in stale_ids, body
    assert all("key_hash" not in item for item in body["stale_keys"]), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)