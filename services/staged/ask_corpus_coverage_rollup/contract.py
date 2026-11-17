from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


def _load_router():
    module = importlib.import_module("services.staged.ask_corpus_coverage_rollup.router")
    for name in ("router", "api_router"):
        value = getattr(module, name, None)
        if value is not None:
            return value
    raise RuntimeError("ask_corpus_coverage_rollup router was not found")


def _seed(session):
    registry = McpServerRegistry.__table__
    corpus = AskCorpusDoc.__table__

    registry.create(session.get_bind(), checkfirst=True)
    corpus.create(session.get_bind(), checkfirst=True)

    now = datetime.now(timezone.utc)
    session.execute(
        registry.insert().values(
            server_id="server-alpha",
            name="Alpha",
            risk_tier="low",
            verdict="approved",
        )
    )
    session.execute(
        registry.insert().values(
            server_id="server-beta",
            name="Beta",
            risk_tier="medium",
            verdict="approved",
        )
    )
    session.execute(
        registry.insert().values(
            server_id="server-unindexed",
            name="Unindexed",
            risk_tier="high",
            verdict="review",
        )
    )
    session.execute(
        corpus.insert().values(
            server_id="server-alpha",
            snippet="alpha",
            terms="alpha",
            content_hash="alpha-hash",
            indexed_at=now,
        )
    )
    session.execute(
        corpus.insert().values(
            server_id="server-beta",
            snippet="beta",
            terms="beta",
            content_hash="beta-hash",
            indexed_at=now - timedelta(days=1),
        )
    )
    session.commit()


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    test_app = FastAPI()
    test_app.include_router(_load_router())

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with SessionLocal() as session:
            _seed(session)

        with TestClient(test_app) as client:
            response = client.get("/api/ask/corpus/coverage")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total_servers"] == 3
            assert payload["indexed_servers"] == 2
            assert 60 <= payload["coverage_pct"] <= 70
            assert any(
                gap.get("server_id") == "server-unindexed"
                for gap in payload["top_gaps"]
            )
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()
    raise SystemExit(0)