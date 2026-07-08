from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from datetime import datetime
from unittest.mock import MagicMock

router = APIRouter(prefix="/health", tags=["health"])


class AskCorpusHealthResponse(BaseModel):
    total_docs: int
    unique_servers: int
    last_indexed: str | None
    index_age_seconds: int | None


def get_write_service_client() -> httpx.Client:
    return httpx.Client()


async def check_ask_corpus_health(client: httpx.Client = Depends(get_write_service_client)) -> AskCorpusHealthResponse:
    sql = """
        SELECT 
            (SELECT COUNT(*) FROM ask_corpus_index) AS total_docs,
            (SELECT COUNT(DISTINCT server_id) FROM ask_corpus_index) AS unique_servers,
            (SELECT MAX(indexed_at) FROM ask_corpus_index) AS last_indexed
    """
    try:
        resp = client.request("POST", "http://127.0.0.1:8772/query", json={"sql": sql, "params": {}})
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        total_docs = 0
        unique_servers = 0
        last_indexed_str = None
        if rows and len(rows[0]) >= 3:
            total_docs = rows[0][0] or 0
            unique_servers = rows[0][1] or 0
            last_indexed_str = rows[0][2]
        index_age_seconds = None
        if last_indexed_str:
            try:
                indexed = datetime.fromisoformat(last_indexed_str.replace('Z', '+00:00'))
                now = datetime.now(indexed.tzinfo) if indexed.tzinfo else datetime.utcnow()
                index_age_seconds = int((now - indexed).total_seconds())
            except Exception:
                index_age_seconds = None
        return AskCorpusHealthResponse(
            total_docs=total_docs,
            unique_servers=unique_servers,
            last_indexed=last_indexed_str,
            index_age_seconds=index_age_seconds
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Write service unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {e}")


router.add_api_route("/ask-corpus", check_ask_corpus_health, methods=["GET"])


def create_mock_client(seeded_rows):
    class MockResponse:
        def __init__(self, data):
            self._data = data
        def json(self):
            return self._data
        def raise_for_status(self):
            pass

    def mock_request(method, url, **kwargs):
        req_data = kwargs.get("json", {})
        sql = req_data.get("sql", "")
        if "COUNT" in sql:
            total_docs = len(seeded_rows)
            unique_servers = len({r["server_id"] for r in seeded_rows})
            last_indexed = max((r["indexed_at"] for r in seeded_rows), default=None)
            return MockResponse({
                "columns": ["total_docs", "unique_servers", "last_indexed"],
                "rows": [[total_docs, unique_servers, last_indexed]],
                "row_count": 1
            })
        return MockResponse({
            "columns": ["server_id", "snippet", "terms", "content_hash", "indexed_at"],
            "rows": [[r["server_id"], r["snippet"], r["terms"], r["content_hash"], r["indexed_at"]] for r in seeded_rows],
            "row_count": len(seeded_rows)
        })

    mock = MagicMock()
    mock.request = mock_request
    return mock


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import threading
    import time

    seeded_rows = [
        {"server_id": "srv1", "snippet": "doc1", "terms": "term1", "content_hash": "hash1", "indexed_at": "2024-01-15T10:00:00"},
        {"server_id": "srv1", "snippet": "doc2", "terms": "term2", "content_hash": "hash2", "indexed_at": "2024-01-15T11:00:00"},
        {"server_id": "srv2", "snippet": "doc3", "terms": "term3", "content_hash": "hash3", "indexed_at": "2024-01-15T12:00:00"},
    ]

    mock_client = create_mock_client(seeded_rows)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_write_service_client] = lambda: mock_client

    with TestClient(app) as client:
        response = client.get("/health/ask-corpus")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "total_docs" in data
        assert "unique_servers" in data
        assert data["total_docs"] >= 0
        assert data["unique_servers"] >= 0
    print("PASS")