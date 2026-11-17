from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_corpus_terms_depth

router = APIRouter(prefix="/api", tags=["ask-corpus"])


class TopTerm(BaseModel):
    term: str
    doc_count: int


class CorpusTermsDepthResponse(BaseModel):
    total_docs: int
    servers_indexed: int
    avg_terms_per_doc: float
    median_terms_per_doc: float
    term_count_histogram: Dict[str, int]
    top_terms: List[TopTerm]
    generated_at: str


@router.get(
    "/ask/corpus/terms-depth",
    response_model=CorpusTermsDepthResponse,
    status_code=200,
)
def ask_corpus_terms_depth(
    session: Session = Depends(get_session),
) -> CorpusTermsDepthResponse:
    return get_corpus_terms_depth(session)


if __name__ == "__main__":
    import json
    from datetime import datetime, timezone

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
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE ask_corpus_index (
                server_id TEXT NOT NULL,
                terms TEXT,
                content_hash TEXT NOT NULL,
                indexed_at TIMESTAMP,
                snippet TEXT
            )
            """
        )
        rows = [
            ([], "00000000-0000-0000-0000-000000000001"),
            (["shared"], "00000000-0000-0000-0000-000000000002"),
            (
                ["shared", "beta", "gamma"],
                "00000000-0000-0000-0000-000000000003",
            ),
            (
                [
                    "shared",
                    "delta",
                    "epsilon",
                    "zeta",
                    "eta",
                    "theta",
                    "iota",
                    "kappa",
                ],
                "00000000-0000-0000-0000-000000000004",
            ),
        ]
        for index, (terms, server_id) in enumerate(rows, start=1):
            connection.exec_driver_sql(
                """
                INSERT INTO ask_corpus_index
                    (server_id, terms, content_hash, indexed_at, snippet)
                VALUES
                    (:server_id, :terms, :content_hash, :indexed_at, :snippet)
                """,
                {
                    "server_id": server_id,
                    "terms": json.dumps(terms),
                    "content_hash": f"hash-{index}",
                    "indexed_at": datetime.now(timezone.utc),
                    "snippet": "",
                },
            )

    test_session_factory = sessionmaker(bind=engine, autoflush=False)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(local_app).get("/api/ask/corpus/terms-depth")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["total_docs"] == 4
    assert payload["servers_indexed"] == 4
    assert payload["avg_terms_per_doc"] == 3.0
    assert payload["median_terms_per_doc"] == 2.0
    assert sum(payload["term_count_histogram"].values()) == 4
    assert payload["term_count_histogram"] == {
        "0": 1,
        "1": 1,
        "2-3": 1,
        "4-7": 0,
        "8+": 1,
    }
    assert any(
        item["term"] == "shared" and item["doc_count"] >= 2
        for item in payload["top_terms"]
    )
    datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))

    print("PASS")