from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry
from .logic import get_orphan_index

router = APIRouter(prefix="/api", tags=["ask-corpus-orphans"])


class OrphanDocument(BaseModel):
    server_id: str
    snippet: str
    terms: list[str]
    content_hash: str
    indexed_at: datetime


class OrphanIndexResponse(BaseModel):
    total: int
    orphans: list[OrphanDocument]


@router.get(
    "/ask/corpus/orphans",
    response_model=OrphanIndexResponse,
    summary="List corpus documents for servers absent from the registry",
)
def get_orphan_documents(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> OrphanIndexResponse:
    result: dict[str, Any] = get_orphan_index(session, limit=limit)
    return OrphanIndexResponse(**result)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    AskCorpusDoc.__table__.create(engine)

    with SessionLocal() as session:
        session.add(McpServerRegistry(server_id="registered-server"))
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="orphan-one",
                    snippet="First orphaned corpus document",
                    terms=["alpha", "beta"],
                    content_hash="hash-one",
                    indexed_at=datetime(2025, 1, 3, 12, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="orphan-two",
                    snippet="Second orphaned corpus document",
                    terms=["gamma"],
                    content_hash="hash-two",
                    indexed_at=datetime(2025, 1, 2, 12, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="registered-server",
                    snippet="Registered corpus document",
                    terms=["delta"],
                    content_hash="hash-three",
                    indexed_at=datetime(2025, 1, 1, 12, 0, 0),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/ask/corpus/orphans")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert {item["server_id"] for item in payload["orphans"]} == {
                "orphan-one",
                "orphan-two",
            }
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()