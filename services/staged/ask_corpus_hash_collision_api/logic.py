from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def _as_string(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_hash_collisions(session: Session, limit: int = 200) -> dict[str, Any]:
    """Return duplicate corpus documents grouped by their content hash."""
    corpus = AskCorpusDoc.__table__

    total_docs, distinct_hashes = session.execute(
        select(
            func.count(),
            func.count(func.distinct(corpus.c.content_hash)),
        ).select_from(corpus)
    ).one()

    grouped = session.execute(
        select(
            corpus.c.content_hash,
            func.count().label("document_count"),
        )
        .where(corpus.c.content_hash.is_not(None))
        .group_by(corpus.c.content_hash)
        .having(func.count() > 1)
        .order_by(
            func.count().desc(),
            corpus.c.content_hash.asc(),
        )
        .limit(limit)
    ).all()

    collision_groups: list[dict[str, Any]] = []
    for content_hash, count in grouped:
        documents = session.execute(
            select(
                corpus.c.server_id,
                corpus.c.indexed_at,
            )
            .where(corpus.c.content_hash == content_hash)
            .order_by(
                corpus.c.server_id.asc(),
                corpus.c.indexed_at.asc(),
            )
        ).all()

        collision_groups.append(
            {
                "content_hash": _as_string(content_hash),
                "count": int(count),
                "server_ids": [_as_string(server_id) for server_id, _ in documents],
                "indexed_at": [_as_string(indexed_at) for _, indexed_at in documents],
            }
        )

    return {
        "total_docs": int(total_docs),
        "distinct_hashes": int(distinct_hashes),
        "collision_groups": collision_groups,
    }


def get_hash_collision_data(session: Session, limit: int = 200) -> dict[str, Any]:
    return get_hash_collisions(session=session, limit=limit)


def get_corpus_hash_collisions(session: Session, limit: int = 200) -> dict[str, Any]:
    return get_hash_collisions(session=session, limit=limit)


def get_hash_collisions_endpoint(
    session: Session = Depends(get_session),
    limit: int = 200,
) -> dict[str, Any]:
    return get_hash_collisions(session=session, limit=limit)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    now = datetime.utcnow()
    test_session.add_all(
        [
            AskCorpusDoc(
                server_id="server-a",
                snippet="duplicate one",
                terms="duplicate",
                content_hash="hash-duplicate",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-b",
                snippet="duplicate two",
                terms="duplicate",
                content_hash="hash-duplicate",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-c",
                snippet="unique one",
                terms="unique",
                content_hash="hash-unique-one",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-d",
                snippet="unique two",
                terms="unique",
                content_hash="hash-unique-two",
                indexed_at=now,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/ask/corpus/hash-collisions")
    def hash_collisions_route(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_hash_collisions(session=session)

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/ask/corpus/hash-collisions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_docs"] == 4
    assert payload["distinct_hashes"] == 3
    assert len(payload["collision_groups"]) == 1
    assert payload["collision_groups"][0]["count"] == 2

    test_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()