from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_hash_collisions


class HashCollisionGroup(BaseModel):
    content_hash: str
    count: int
    server_ids: List[str]
    indexed_at: List[str]


class HashCollisionResponse(BaseModel):
    total_docs: int
    distinct_hashes: int
    collision_groups: List[HashCollisionGroup]


router = APIRouter(prefix="/api")


@router.get(
    "/ask/corpus/hash-collisions",
    response_model=HashCollisionResponse,
)
def list_hash_collisions(
    session: Session = Depends(get_session),
    limit: int = Query(default=200, ge=1),
) -> HashCollisionResponse:
    return get_hash_collisions(session, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import AskCorpusDoc

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        test_session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="duplicate document",
                    terms="alpha,beta",
                    content_hash="duplicate-hash",
                    indexed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="duplicate document",
                    terms="alpha,beta",
                    content_hash="duplicate-hash",
                    indexed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-c",
                    snippet="unique document c",
                    terms="gamma",
                    content_hash="unique-hash-c",
                    indexed_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-d",
                    snippet="unique document d",
                    terms="delta",
                    content_hash="unique-hash-d",
                    indexed_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
                ),
            ]
        )
        test_session.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            yield test_session

        test_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(test_app).get("/api/ask/corpus/hash-collisions")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total_docs"] == 4
        assert payload["distinct_hashes"] == 3
        assert len(payload["collision_groups"]) == 1
        assert payload["collision_groups"][0]["count"] == 2

    print("PASS")