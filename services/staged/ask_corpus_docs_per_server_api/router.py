from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry
from .logic import get_corpus_docs_per_server

router = APIRouter(prefix="/api", tags=["ask-corpus"])


@router.get("/ask/corpus-docs-per-server")
def ask_corpus_docs_per_server(
    limit: int = Query(default=50, ge=1),
    min_docs: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    return get_corpus_docs_per_server(
        session=session,
        limit=limit,
        min_docs=min_docs,
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    assert any(
        route.path == "/api/ask/corpus-docs-per-server"
        for route in test_app.routes
    )
    print("PASS")