from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


class DuplicateGroup(BaseModel):
    content_hash: str
    document_count: int
    server_ids: List[str]
    latest_indexed_at: datetime


class StaleDocuments(BaseModel):
    count: int
    oldest_indexed_at: Optional[datetime] = None


class DuplicateHashResponse(BaseModel):
    total_documents: int
    unique_hashes: int
    duplicate_groups: List[DuplicateGroup]
    stale_documents: StaleDocuments


def create_contract_app() -> FastAPI:
    contract_app = FastAPI()

    @contract_app.get(
        "/api/ask/corpus/duplicate-hashes",
        response_model=DuplicateHashResponse,
    )
    def duplicate_hashes(
        limit: int = Query(default=50, ge=1, le=200),
        age_days: int = Query(default=30, ge=0, le=3650),
        session: Session = Depends(get_session),
    ) -> DuplicateHashResponse:
        total_documents = session.execute(
            select(func.count()).select_from(AskCorpusDoc)
        ).scalar_one()

        unique_hashes = session.execute(
            select(func.count(func.distinct(AskCorpusDoc.content_hash)))
        ).scalar_one()

        duplicate_rows = session.execute(
            select(
                AskCorpusDoc.content_hash,
                func.count().label("document_count"),
            )
            .group_by(AskCorpusDoc.content_hash)
            .having(func.count() > 1)
            .order_by(AskCorpusDoc.content_hash)
            .limit(limit)
        ).all()

        duplicate_groups: List[DuplicateGroup] = []
        for content_hash, document_count in duplicate_rows:
            documents = session.execute(
                select(AskCorpusDoc)
                .where(AskCorpusDoc.content_hash == content_hash)
                .order_by(AskCorpusDoc.server_id, AskCorpusDoc.indexed_at)
            ).scalars().all()
            duplicate_groups.append(
                DuplicateGroup(
                    content_hash=content_hash,
                    document_count=document_count,
                    server_ids=list(
                        dict.fromkeys(document.server_id for document in documents)
                    ),
                    latest_indexed_at=max(
                        document.indexed_at for document in documents
                    ),
                )
            )

        cutoff = datetime.utcnow() - timedelta(days=age_days)
        stale_count = session.execute(
            select(func.count())
            .select_from(AskCorpusDoc)
            .where(AskCorpusDoc.indexed_at < cutoff)
        ).scalar_one()
        oldest_stale = session.execute(
            select(func.min(AskCorpusDoc.indexed_at)).where(
                AskCorpusDoc.indexed_at < cutoff
            )
        ).scalar_one_or_none()

        return DuplicateHashResponse(
            total_documents=total_documents,
            unique_hashes=unique_hashes,
            duplicate_groups=duplicate_groups,
            stale_documents=StaleDocuments(
                count=stale_count,
                oldest_indexed_at=oldest_stale,
            ),
        )

    return contract_app


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_session():
        with test_session_factory() as session:
            yield session

    contract_app = create_contract_app()
    contract_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with test_session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="first duplicate",
                    terms="duplicate",
                    content_hash="hash-shared",
                    indexed_at=now - timedelta(days=1),
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="second duplicate",
                    terms="duplicate",
                    content_hash="hash-shared",
                    indexed_at=now - timedelta(days=2),
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="unique current document",
                    terms="unique",
                    content_hash="hash-current",
                    indexed_at=now - timedelta(days=3),
                ),
                AskCorpusDoc(
                    server_id="server-4",
                    snippet="unique stale document",
                    terms="stale",
                    content_hash="hash-stale",
                    indexed_at=now - timedelta(days=45),
                ),
            ]
        )
        session.commit()

    with TestClient(contract_app) as client:
        response = client.get(
            "/api/ask/corpus/duplicate-hashes",
            params={"limit": 50, "age_days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_documents"] == 4
        assert payload["unique_hashes"] == 3
        assert len(payload["duplicate_groups"]) == 1
        assert payload["duplicate_groups"][0]["content_hash"] == "hash-shared"
        assert payload["duplicate_groups"][0]["document_count"] == 2
        assert payload["stale_documents"]["count"] == 1

    print("PASS")


if __name__ == "__main__":
    main()