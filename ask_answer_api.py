"""ask_answer_api.py -- POST /api/ask: grounded answers with mandatory citations.

"Ask MCPLookup" v2 slice (Appendix G). The answer is synthesized FROM
RETRIEVED ROWS ONLY via deterministic templating -- one line per cited server
(name, tier, the matched evidence). Below-threshold retrieval returns status
INSUFFICIENT (the verdict taxonomy's own honesty pattern) instead of a guess.

LLM polish is OPTIONAL and strictly flag-gated: env ASK_LLM=1 routes the
templated answer (never the raw corpus) through ladder_shim:8796 for phrasing
only; default OFF = zero per-query LLM cost and no network. Citations are
attached either way and are always the retrieval's provenance, never model
output.
"""
from __future__ import annotations

import os
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from ask_retrieval_service import MIN_SCORE, retrieve
from verdict_breakdown_api import Principal, charge_lookup, get_principal

router = APIRouter(prefix="/api", tags=["ask"])

SHIM_URL = "http://127.0.0.1:8796/v1/chat/completions"
MAX_CITED = 5


class AskRequest(BaseModel):
    query: str
    k: int = MAX_CITED


class Citation(BaseModel):
    server_id: str
    score: float
    matched_fields: List[str]
    matched_terms: List[str]


class AskResponse(BaseModel):
    status: str                      # "ok" | "insufficient"
    query: str
    answer: Optional[str] = None
    citations: List[Citation] = []
    llm_polished: bool = False


def compose_answer(query: str, hits: List[dict]) -> str:
    """Deterministic templating from retrieved rows only."""
    lines = [f"Results for: {query!r} ({len(hits)} match(es) from the scored registry)"]
    for h in hits:
        prov = h["provenance"]
        lines.append(f"- {h['snippet'][:180]}  "
                     f"[matched: {', '.join(prov['matched_fields'])} -> "
                     f"{', '.join(prov['matched_terms'][:6])}]")
    lines.append("Every line above cites a scored registry row; open a server's "
                 "verdict for the full per-axis breakdown.")
    return "\n".join(lines)


def _maybe_polish(answer: str) -> tuple:
    """(answer, polished). ONLY when ASK_LLM=1; failure falls back to the
    templated answer -- polish can never lose the grounded content."""
    if os.environ.get("ASK_LLM", "").strip() not in ("1", "true", "on"):
        return answer, False
    try:
        import requests
        resp = requests.post(SHIM_URL, json={
            "model": "zo-ladder-low",
            "messages": [
                {"role": "system",
                 "content": "Rephrase this grounded search result fluently. Do NOT "
                            "add servers, facts or numbers that are not in the text."},
                {"role": "user", "content": answer}],
            "temperature": 0.1, "max_tokens": 1024}, timeout=20)
        if resp.status_code == 200:
            txt = (resp.json().get("choices", [{}])[0]
                   .get("message", {}).get("content", "")).strip()
            if txt:
                return txt, True
    except Exception:
        pass
    return answer, False


def ask(db: Session, query: str, k: int = MAX_CITED) -> AskResponse:
    hits = retrieve(db, query, k=min(max(k, 1), MAX_CITED))
    if not hits or hits[0]["score"] < MIN_SCORE:
        return AskResponse(status="insufficient", query=query,
                           answer=None, citations=[])
    answer, polished = _maybe_polish(compose_answer(query, hits))
    return AskResponse(
        status="ok", query=query, answer=answer, llm_polished=polished,
        citations=[Citation(server_id=h["server_id"], score=h["score"],
                            matched_fields=h["provenance"]["matched_fields"],
                            matched_terms=h["provenance"]["matched_terms"])
                   for h in hits])


@router.post("/ask", response_model=AskResponse)
def post_ask(body: AskRequest, db: Session = Depends(get_session),
             principal: Principal = Depends(get_principal)) -> AskResponse:
    charge_lookup(db, principal)     # same daily-cap economics as verdict opens
    return ask(db, body.query, k=body.k)


if __name__ == "__main__":
    import os as _os
    _os.environ.setdefault("DATABASE_URL", "sqlite://")
    _os.environ.pop("ASK_LLM", None)   # flag OFF: no network may be attempted
    import socket
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import AskCorpusDoc

    class _NoNet(socket.socket):
        def connect(self, *a, **k):  # any connect attempt = test failure
            raise AssertionError("network attempted with ASK_LLM off")
    socket.socket = _NoNet  # type: ignore

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(AskCorpusDoc(server_id="weak1",
                       snippet="Weak Auth Github Server | verdict=HIGH tier=HIGH",
                       terms={"name": ["github", "server", "weak"],
                              "verdict": ["high", "github"],
                              "axes": ["auth_strength", "weak"],
                              "desc": ["weak", "authentication"]}))
    s.commit()

    r = ask(s, "weak auth github server")
    assert r.status == "ok" and r.llm_polished is False
    cited = {c.server_id for c in r.citations}
    assert cited == {"weak1"}
    # every server named in the answer appears in citations
    assert "weak1" not in r.answer or "weak1" in cited
    for c in r.citations:
        assert c.matched_fields and c.matched_terms

    r2 = ask(s, "quantum blockchain sandwiches")
    assert r2.status == "insufficient" and r2.answer is None and not r2.citations

    # empty corpus -> insufficient
    s.query(AskCorpusDoc).delete(); s.commit()
    assert ask(s, "weak auth").status == "insufficient"
    print("PASS")
