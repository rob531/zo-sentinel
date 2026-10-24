from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import (
    Column,
    DateTime,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


metadata = MetaData()

ask_corpus_index = Table(
    "ask_corpus_index",
    metadata,
    Column("server_id", String, nullable=False),
    Column("snippet", Text),
    Column("terms", JSON),
    Column("content_hash", String),
    Column("indexed_at", DateTime, nullable=False),
)

McpServerRegistry = Table(
    "mcp_server_registry",
    metadata,
    Column("server_id", String, nullable=False),
    Column("name", String),
    Column("last_seen", DateTime),
)

router = APIRouter()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _calculate_age_days(indexed_at: datetime, now: datetime) -> int:
    age = now - _as_utc(indexed_at)
    if age.total_seconds() <= 0:
        return 0
    return int(age.total_seconds() // 86400)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def build_staleness_report(session, days: int) -> dict:
    now = _utc_now()

    statement = (
        select(
            ask_corpus_index.c.server_id,
            McpServerRegistry.c.name,
            func.min(ask_corpus_index.c.indexed_at).label("indexed_at"),
            func.count().label("doc_count"),
        )
        .select_from(
            ask_corpus_index.outerjoin(
                McpServerRegistry,
                ask_corpus_index.c.server_id == McpServerRegistry.c.server_id,
            )
        )
        .group_by(
            ask_corpus_index.c.server_id,
            McpServerRegistry.c.name,
        )
    )

    servers = []
    for server_id, name, indexed_at, doc_count in session.execute(statement):
        age_days = _calculate_age_days(indexed_at, now)
        servers.append(
            {
                "server_id": server_id,
                "name": name if name is not None else server_id,
                "indexed_at": _as_utc(indexed_at).isoformat(),
                "age_days": age_days,
                "doc_count": int(doc_count),
                "_bucket": _bucket_for_age(age_days),
            }
        )

    bucket_names = ("0-7", "8-30", "31-90", "90+")
    buckets = [
        {"tier": tier, "count": sum(1 for server in servers if server["_bucket"] == tier)}
        for tier in bucket_names
    ]

    stale = [
        {
            "server_id": server["server_id"],
            "name": server["name"],
            "indexed_at": server["indexed_at"],
            "age_days": server["age_days"],
            "doc_count": server["doc_count"],
        }
        for server in servers
        if server["age_days"] > days
    ]
    stale.sort(key=lambda server: (-server["age_days"], server["server_id"]))

    servers_indexed = len(servers)
    stale_servers = len(stale)
    stale_pct = (
        round((stale_servers / servers_indexed) * 100, 2)
        if servers_indexed
        else 0.0
    )

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "buckets": buckets,
        "stale": stale,
        "totals": {
            "servers_indexed": servers_indexed,
            "stale_servers": stale_servers,
            "stale_pct": stale_pct,
        },
    }


@router.get("/api/ask/corpus/staleness")
def corpus_staleness(
    days: int = Query(default=30, ge=0),
    session=Depends(get_session),
):
    return build_staleness_report(session, days)


def create_test_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    metadata.create_all(engine)

    app = FastAPI(title="Ask corpus document staleness acceptance contract")
    app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return app, testing_session


def run_self_test() -> None:
    app, testing_session = create_test_app()
    now = datetime.now(timezone.utc)

    with testing_session() as session:
        session.execute(
            ask_corpus_index.insert(),
            [
                {
                    "server_id": "fresh-server",
                    "snippet": "Fresh documentation",
                    "terms": {"fresh": True},
                    "content_hash": "fresh-hash",
                    "indexed_at": now - timedelta(days=0),
                },
                {
                    "server_id": "aging-server",
                    "snippet": "Aging documentation",
                    "terms": {"aging": True},
                    "content_hash": "aging-hash",
                    "indexed_at": now - timedelta(days=10),
                },
                {
                    "server_id": "stale-server",
                    "snippet": "Stale documentation",
                    "terms": {"stale": True},
                    "content_hash": "stale-hash",
                    "indexed_at": now - timedelta(days=120),
                },
            ],
        )
        session.execute(
            McpServerRegistry.insert(),
            [
                {
                    "server_id": "fresh-server",
                    "name": "Fresh Server",
                    "last_seen": now,
                },
                {
                    "server_id": "aging-server",
                    "name": "Aging Server",
                    "last_seen": now,
                },
                {
                    "server_id": "stale-server",
                    "name": "Stale Server",
                    "last_seen": now,
                },
            ],
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/ask/corpus/staleness", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()

    assert sum(bucket["count"] for bucket in payload["buckets"]) == 3
    assert payload["stale"], payload
    assert payload["stale"][0]["server_id"] == "stale-server"
    assert payload["stale"][0]["age_days"] >= 120
    assert "stale_pct" in payload["totals"]
    assert payload["totals"]["servers_indexed"] == 3
    assert payload["totals"]["stale_servers"] == 1

    print("PASS")


if __name__ == "__main__":
    run_self_test()