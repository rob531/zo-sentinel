from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def get_hash_duplicates(
    db: Session = Depends(get_session),
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    current_session = session if session is not None else db

    total_rows = int(
        current_session.execute(
            select(func.count()).select_from(AskCorpusDoc)
        ).scalar_one()
    )

    distinct_hashes = int(
        current_session.execute(
            select(func.count(func.distinct(AskCorpusDoc.content_hash)))
        ).scalar_one()
    )

    duplicate_rows = current_session.execute(
        select(
            AskCorpusDoc.content_hash,
            func.count().label("row_count"),
            func.max(AskCorpusDoc.indexed_at).label("latest_indexed_at"),
        )
        .where(AskCorpusDoc.content_hash.is_not(None))
        .group_by(AskCorpusDoc.content_hash)
        .having(func.count() > 1)
        .order_by(AskCorpusDoc.content_hash)
    ).all()

    duplicate_hashes = [row.content_hash for row in duplicate_rows]
    server_ids_by_hash: Dict[str, List[str]] = {}

    if duplicate_hashes:
        server_rows = current_session.execute(
            select(AskCorpusDoc.content_hash, AskCorpusDoc.server_id)
            .where(
                AskCorpusDoc.content_hash.in_(duplicate_hashes),
                AskCorpusDoc.server_id.is_not(None),
            )
            .distinct()
            .order_by(AskCorpusDoc.content_hash, AskCorpusDoc.server_id)
        ).all()

        for content_hash, server_id in server_rows:
            server_ids_by_hash.setdefault(str(content_hash), []).append(str(server_id))

    duplicates = [
        {
            "content_hash": str(row.content_hash),
            "server_ids": server_ids_by_hash.get(str(row.content_hash), []),
            "row_count": int(row.row_count),
            "latest_indexed_at": _isoformat(row.latest_indexed_at),
        }
        for row in duplicate_rows
    ]

    return {
        "duplicates": duplicates,
        "total_rows": total_rows,
        "distinct_hashes": distinct_hashes,
        "duplicate_groups": len(duplicates),
    }


get_corpus_hash_duplicates = get_hash_duplicates


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    testing_session_factory = sessionmaker(bind=engine)

    with testing_session_factory() as test_session:
        test_session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="first document",
                    terms=[],
                    content_hash="h1",
                    indexed_at=datetime(2024, 1, 1, 12, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="second document",
                    terms=[],
                    content_hash="h1",
                    indexed_at=datetime(2024, 1, 2, 12, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="server-c",
                    snippet="third document",
                    terms=[],
                    content_hash="h2",
                    indexed_at=datetime(2024, 1, 3, 12, 0, 0),
                ),
            ]
        )
        test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/ask/corpus/hash-duplicates")
    def hash_duplicates_endpoint(db: Session = Depends(get_session)):
        return get_hash_duplicates(db=db)

    def override_get_session():
        with testing_session_factory() as test_session:
            yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus/hash-duplicates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 3
    assert payload["distinct_hashes"] == 2
    assert payload["duplicate_groups"] == 1
    assert len(payload["duplicates"][0]["server_ids"]) == 2
    print("PASS")