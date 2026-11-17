from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


class DuplicateHashGroup(BaseModel):
    content_hash: str
    document_count: int
    server_ids: List[str]
    latest_indexed_at: Optional[datetime]


class StaleDocuments(BaseModel):
    count: int
    oldest_indexed_at: Optional[datetime]


class DuplicateHashReport(BaseModel):
    total_documents: int
    unique_hashes: int
    duplicate_groups: List[DuplicateHashGroup]
    stale_documents: StaleDocuments


DuplicateHashResponse = DuplicateHashReport


def get_duplicate_hash_report(
    db: Session = Depends(get_session),
    limit: int = 50,
    age_days: int = 30,
) -> dict:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    if age_days < 0 or age_days > 3650:
        raise ValueError("age_days must be between 0 and 3650")

    total_documents = (
        db.execute(select(func.count()).select_from(AskCorpusDoc)).scalar_one()
        or 0
    )
    unique_hashes = (
        db.execute(
            select(func.count(func.distinct(AskCorpusDoc.content_hash)))
        ).scalar_one()
        or 0
    )

    duplicate_rows = db.execute(
        select(
            AskCorpusDoc.content_hash,
            func.count().label("document_count"),
            func.max(AskCorpusDoc.indexed_at).label("latest_indexed_at"),
        )
        .where(AskCorpusDoc.content_hash.is_not(None))
        .group_by(AskCorpusDoc.content_hash)
        .having(func.count() > 1)
        .order_by(
            func.count().desc(),
            func.max(AskCorpusDoc.indexed_at).desc(),
            AskCorpusDoc.content_hash.asc(),
        )
        .limit(limit)
    ).all()

    server_ids_by_hash = {}
    hashes = [row.content_hash for row in duplicate_rows]
    if hashes:
        document_rows = db.execute(
            select(
                AskCorpusDoc.content_hash,
                AskCorpusDoc.server_id,
            )
            .where(AskCorpusDoc.content_hash.in_(hashes))
            .order_by(
                AskCorpusDoc.content_hash.asc(),
                AskCorpusDoc.server_id.asc(),
            )
        ).all()
        for document in document_rows:
            if document.server_id is None:
                continue
            server_ids_by_hash.setdefault(document.content_hash, []).append(
                str(document.server_id)
            )

    duplicate_groups = [
        {
            "content_hash": str(row.content_hash),
            "document_count": int(row.document_count),
            "server_ids": server_ids_by_hash.get(row.content_hash, []),
            "latest_indexed_at": row.latest_indexed_at,
        }
        for row in duplicate_rows
    ]

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=age_days
    )
    stale_count, oldest_indexed_at = db.execute(
        select(
            func.count(),
            func.min(AskCorpusDoc.indexed_at),
        ).where(AskCorpusDoc.indexed_at < cutoff)
    ).one()

    return {
        "total_documents": int(total_documents),
        "unique_hashes": int(unique_hashes),
        "duplicate_groups": duplicate_groups,
        "stale_documents": {
            "count": int(stale_count or 0),
            "oldest_indexed_at": oldest_indexed_at,
        },
    }


get_duplicate_hashes = get_duplicate_hash_report
get_duplicate_hash_data = get_duplicate_hash_report
get_duplicate_hash_summary = get_duplicate_hash_report


def duplicate_hashes_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    age_days: int = Query(default=30, ge=0, le=3650),
    db: Session = Depends(get_session),
) -> DuplicateHashReport:
    return DuplicateHashReport(
        **get_duplicate_hash_report(
            db=db,
            limit=limit,
            age_days=age_days,
        )
    )


if __name__ == "__main__":
    from datetime import datetime as datetime_type

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(that_engine)
    ThatSession = sessionmaker(bind=that_engine, expire_on_commit=False)
    that_session = ThatSession()

    now = datetime_type.now(timezone.utc).replace(tzinfo=None)
    that_session.add_all(
        [
            AskCorpusDoc(
                server_id="server-a",
                snippet="first shared document",
                terms="alpha shared",
                content_hash="shared-hash",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-b",
                snippet="second shared document",
                terms="beta shared",
                content_hash="shared-hash",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-c",
                snippet="unique document",
                terms="gamma",
                content_hash="unique-hash-a",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-d",
                snippet="old unique document",
                terms="delta",
                content_hash="unique-hash-b",
                indexed_at=now - timedelta(days=45),
            ),
        ]
    )
    that_session.commit()

    def override_get_session():
        yield that_session

    that_app = FastAPI()

    @that_app.get(
        "/api/ask/corpus/duplicate-hashes",
        response_model=DuplicateHashReport,
    )
    def read_duplicate_hashes(
        limit: int = Query(default=50, ge=1, le=200),
        age_days: int = Query(default=30, ge=0, le=3650),
        db: Session = Depends(get_session),
    ):
        return get_duplicate_hash_report(
            db=db,
            limit=limit,
            age_days=age_days,
        )

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get(
        "/api/ask/corpus/duplicate-hashes"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_documents"] == 4
    assert payload["unique_hashes"] == 3
    assert len(payload["duplicate_groups"]) == 1
    assert payload["duplicate_groups"][0]["document_count"] == 2
    assert payload["stale_documents"]["count"] == 1

    print("PASS")