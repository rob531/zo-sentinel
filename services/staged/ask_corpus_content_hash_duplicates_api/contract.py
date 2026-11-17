from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc

from . import router as router_module


router = getattr(router_module, "router", None) or getattr(router_module, "api_router")
if not isinstance(router, APIRouter):
    router = next(
        value for value in vars(router_module).values() if isinstance(value, APIRouter)
    )

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

with engine.begin() as connection:
    connection.execute(
        text(
            """
            CREATE TABLE ask_corpus_index (
                server_id VARCHAR NOT NULL,
                snippet TEXT NOT NULL,
                terms TEXT NOT NULL,
                content_hash VARCHAR NOT NULL,
                indexed_at DATETIME NOT NULL
            )
            """
        )
    )

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    connection.execute(
        text(
            """
            INSERT INTO ask_corpus_index
                (server_id, snippet, terms, content_hash, indexed_at)
            VALUES
                (:server_id, :snippet, :terms, :content_hash, :indexed_at)
            """
        ),
        [
            {
                "server_id": "server-a",
                "snippet": "First duplicate document",
                "terms": "alpha beta",
                "content_hash": "h1",
                "indexed_at": base_time,
            },
            {
                "server_id": "server-b",
                "snippet": "Second duplicate document",
                "terms": "gamma delta",
                "content_hash": "h1",
                "indexed_at": base_time + timedelta(minutes=1),
            },
            {
                "server_id": "server-c",
                "snippet": "Unique document",
                "terms": "epsilon",
                "content_hash": "h2",
                "indexed_at": base_time + timedelta(minutes=2),
            },
            {
                "server_id": "server-d",
                "snippet": "Another unique document",
                "terms": "zeta",
                "content_hash": "h3",
                "indexed_at": base_time + timedelta(minutes=3),
            },
        ],
    )

assert AskCorpusDoc.__tablename__ == "ask_corpus_index"

test_app = FastAPI()
test_app.include_router(router)


def override_get_session():
    with TestingSessionLocal() as session:
        yield session


test_app.dependency_overrides[get_session] = override_get_session

with TestClient(test_app) as client:
    response = client.get("/api/ask/corpus/duplicate-hashes")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["total_docs"] == 4, payload
    assert len(payload["duplicate_groups"]) == 1, payload
    assert payload["duplicate_groups"][0]["content_hash"] == "h1", payload
    assert payload["duplicate_groups"][0]["doc_count"] == 2, payload

print("PASS")