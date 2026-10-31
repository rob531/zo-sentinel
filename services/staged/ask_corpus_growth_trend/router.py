"""HTTP surface for corpus growth trend reporting."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_corpus_growth_trend

router = APIRouter(prefix="/api/ask/corpus-growth", tags=["ask", "corpus-growth"])


class CorpusGrowthPoint(BaseModel):
    date: date
    servers_indexed: int
    docs_indexed: int
    cumulative_docs: int


class CorpusGrowthTotals(BaseModel):
    servers: int
    docs: int


class CorpusGrowthResponse(BaseModel):
    days: int
    series: list[CorpusGrowthPoint]
    totals: CorpusGrowthTotals


@router.get("", response_model=CorpusGrowthResponse)
def corpus_growth_trend(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> CorpusGrowthResponse:
    return CorpusGrowthResponse.model_validate(get_corpus_growth_trend(db, days))


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import AskCorpusDoc

    test_engine = create_engine = None
    from sqlalchemy import create_engine

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def register_date_trunc(dbapi_connection, _connection_record):
        dbapi_connection.create_function(
            "date_trunc",
            2,
            lambda _unit, value: None if value is None else str(value)[:10],
        )

    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="growth-server-1",
                    snippet="first document",
                    terms="corpus growth",
                    content_hash="growth-hash-1",
                    indexed_at=now,
                ),
                AskCorpusDoc(
                    server_id="growth-server-2",
                    snippet="second document",
                    terms="corpus growth",
                    content_hash="growth-hash-2",
                    indexed_at=now,
                ),
                AskCorpusDoc(
                    server_id="growth-server-3",
                    snippet="third document",
                    terms="corpus growth",
                    content_hash="growth-hash-3",
                    indexed_at=now - timedelta(days=1),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/ask/corpus-growth", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["series"]) == 2
    assert sorted(point["docs_indexed"] for point in payload["series"]) == [1, 2]
    assert payload["totals"] == {"servers": 3, "docs": 3}
    assert payload["series"][-1]["cumulative_docs"] == 3
    print("PASS")