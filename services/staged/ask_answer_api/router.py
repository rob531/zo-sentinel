from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import _retrieve, _answer

router = APIRouter()


class AskRequest(BaseModel):
    query: str
    server_id: Optional[int] = None


class AskResponse(BaseModel):
    answer: str
    snippets: List[Dict]
    sources: List[str]


@router.get("/api/ask", response_model=AskResponse)
def ask_get(
    q: str = Query(..., alias="q"),
    server_id: Optional[int] = None,
    db: Session = Depends(get_session),
):
    snippets = _retrieve(q, server_id)
    if not snippets:
        raise HTTPException(status_code=404, detail="No snippets found")
    answer = _answer(q, snippets)
    sources = list({s.get("source") for s in snippets if "source" in s})
    return AskResponse(answer=answer, snippets=snippets, sources=sources)


@router.post("/api/ask", response_model=AskResponse)
def ask_post(req: AskRequest, db: Session = Depends(get_session)):
    snippets = _retrieve(req.query, req.server_id)
    if not snippets:
        raise HTTPException(status_code=404, detail="No snippets found")
    answer = _answer(req.query, snippets)
    sources = list({s.get("source") for s in snippets if "source" in s})
    return AskResponse(answer=answer, snippets=snippets, sources=sources)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite session for the self‑test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session() -> Session:
        return SessionLocal()

    # Monkey‑patch the logic layer with a tiny in‑memory store
    from . import logic as logic_mod

    logic_mod._FAKE_DATA = [
        {"server_id": 1, "text": "Snippet A1", "source": "src1"},
        {"server_id": 1, "text": "Snippet A2", "source": "src2"},
        {"server_id": 2, "text": "Snippet B1", "source": "src3"},
    ]

    def fake_retrieve(query: str, server_id: Optional[int] = None) -> List[Dict]:
        filtered = [
            d
            for d in logic_mod._FAKE_DATA
            if server_id is None or d["server_id"] == server_id
        ]
        return filtered[:5]

    def fake_answer(query: str, snippets: List[Dict]) -> str:
        return "Answer based on: " + " ".join(s["text"] for s in snippets)

    logic_mod._retrieve = fake_retrieve
    logic_mod._answer = fake_answer

    # Build test app
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/api/ask?q=test&server_id=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]
    assert len(payload["snippets"]) >= 1
    print("PASS")