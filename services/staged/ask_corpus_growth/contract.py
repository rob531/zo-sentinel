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
    from app.models import AskCorpusDoc, Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    session = TestingSession()
    session.add(McpServerRegistry(server_id="srv1", name="Server One", risk_tier="HIGH"))
    session.add(McpServerRegistry(server_id="srv2", name="Server Two", risk_tier="MEDIUM"))
    session.add(McpServerRegistry(server_id="srv3", name="Server Three", risk_tier="LOW"))
    session.add(
        AskCorpusDoc(
            server_id="srv1",
            snippet="first document",
            terms="first",
            content_hash="hash-1",
            indexed_at=today,
        )
    )
    session.add(
        AskCorpusDoc(
            server_id="srv2",
            snippet="second document",
            terms="second",
            content_hash="hash-2",
            indexed_at=today,
        )
    )
    session.add(
        AskCorpusDoc(
            server_id="srv3",
            snippet="third document",
            terms="third",
            content_hash="hash-3",
            indexed_at=yesterday,
        )
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/ask/corpus-growth", params={"days": 30})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 30, body
    assert len(body["series"]) == 2, body
    assert body["total_documents"] == 3, body
    assert body["unique_servers"] == 3, body

    by_date = {item["date"]: item for item in body["series"]}
    assert today.date().isoformat() in by_date, body
    assert yesterday.date().isoformat() in by_date, body
    assert by_date[today.date().isoformat()]["documents"] == 2, body
    assert by_date[yesterday.date().isoformat()]["documents"] == 1, body
    assert len(body["top_servers"]) == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)