from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional
import requests
from app.db import get_session
from app.models import Server
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

router = APIRouter()

class CorpusEntry(BaseModel):
    signal_type: str
    snippet: str
    archived_at: datetime
    evidence_hash: str

class SignalEvidenceArchiveResponse(BaseModel):
    server_id: str
    archived_count: int
    total_evidence_bytes: int
    oldest_evidence_date: Optional[datetime]
    newest_evidence_date: Optional[datetime]
    corpus_entries: List[CorpusEntry]

class RefreshResponse(BaseModel):
    server_id: str
    new_entries: int
    updated_entries: int
    skipped_entries: int

def get_write_service_url() -> str:
    return "http://127.0.0.1:8772"

def query_signal_scores(server_id: str, days: int, session: Session = Depends(get_session)) -> List:
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    query = text("""
        SELECT signal_type, evidence_blob, scored_at
        FROM mcp_signal_scores
        WHERE server_id = :server_id AND scored_at <= :cutoff_date
        ORDER BY scored_at DESC
    """)
    result = session.execute(query, {"server_id": server_id, "cutoff_date": cutoff_date})
    return result.fetchall()

def extract_snippet(evidence_blob: str) -> str:
    if len(evidence_blob) > 1000:
        return evidence_blob[:1000] + "..."
    return evidence_blob

def archive_to_corpus(server_id: str, signal_type: str, snippet: str, evidence_hash: str) -> bool:
    url = f"{get_write_service_url()}/query"
    payload = {
        "query": "INSERT INTO ask_corpus_index (server_id, snippet, terms, content_hash, indexed_at) VALUES (:server_id, :snippet, :terms, :content_hash, :indexed_at)",
        "params": {
            "server_id": server_id,
            "snippet": snippet,
            "terms": snippet.lower().split(),
            "content_hash": evidence_hash,
            "indexed_at": datetime.utcnow().isoformat()
        }
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

@router.get("/servers/{server_id}/signal-evidence-archive")
def get_signal_evidence_archive(server_id: str, days: int = Query(default=7, ge=1, le=90), session: Session = Depends(get_session)) -> SignalEvidenceArchiveResponse:
    server = session.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = query_signal_scores(server_id, days, session)
    corpus_entries = []
    total_bytes = 0
    oldest_date = None
    newest_date = None

    for score in scores:
        snippet = extract_snippet(score.evidence_blob)
        evidence_hash = hash(score.evidence_blob)
        corpus_entries.append(CorpusEntry(
            signal_type=score.signal_type,
            snippet=snippet,
            archived_at=score.scored_at,
            evidence_hash=str(evidence_hash)
        ))
        total_bytes += len(score.evidence_blob)
        if oldest_date is None or score.scored_at < oldest_date:
            oldest_date = score.scored_at
        if newest_date is None or score.scored_at > newest_date:
            newest_date = score.scored_at

    return SignalEvidenceArchiveResponse(
        server_id=server_id,
        archived_count=len(corpus_entries),
        total_evidence_bytes=total_bytes,
        oldest_evidence_date=oldest_date,
        newest_evidence_date=newest_date,
        corpus_entries=corpus_entries[-50:] if len(corpus_entries) > 50 else corpus_entries
    )

@router.post("/servers/{server_id}/signal-evidence-archive/refresh")
def refresh_signal_evidence_archive(server_id: str, session: Session = Depends(get_session)) -> RefreshResponse:
    server = session.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = query_signal_scores(server_id, 7, session)
    new_entries = 0
    updated_entries = 0
    skipped_entries = 0

    for score in scores:
        snippet = extract_snippet(score.evidence_blob)
        evidence_hash = hash(score.evidence_blob)
        if archive_to_corpus(server_id, score.signal_type, snippet, str(evidence_hash)):
            new_entries += 1
        else:
            skipped_entries += 1

    return RefreshResponse(
        server_id=server_id,
        new_entries=new_entries,
        updated_entries=updated_entries,
        skipped_entries=skipped_entries
    )

if __name__ == '__main__':
    from fastapi import FastAPI
    from app.db import get_session
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    with patch('requests.post') as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {'rows': []})
        client = TestClient(app)
        resp = client.get("/servers/test-srv/signal-evidence-archive")
        assert resp.status_code == 200
        data = resp.json()
        assert 'archived_count' in data and 'corpus_entries' in data
        print("PASS: signal evidence archive API")