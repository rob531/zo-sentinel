import logging
from typing import Any, Optional
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()
app = FastAPI(title="Verdict Change Feed Service")

class VerdictChange(BaseModel):
    signal_id: str
    verdict: str
    previous_verdict: Optional[str] = None
    timestamp: Optional[str] = None

class VerdictChangeResponse(BaseModel):
    verdict_changes: list[VerdictChange]
    total: int

@router.get("/verdict-change-feed", response_model=VerdictChangeResponse)
async def get_verdict_change_feed(
    signal_ids: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session)
) -> VerdictChangeResponse:
    try:
        signal_id_list = signal_ids.split(",") if signal_ids else []
        logger.info(f"Fetching verdict changes for signal_ids: {signal_id_list}")
        verdict_changes = _fetch_verdict_changes(signal_id_list, limit, offset, session)
        return VerdictChangeResponse(verdict_changes=verdict_changes, total=len(verdict_changes))
    except Exception as e:
        logger.error(f"Error fetching verdict change feed: {e}")
        raise

@router.get("/mesh-scores")
async def mesh_scores_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_signal_scores()

@router.get("/mesh-memory")
async def mesh_memory_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_mesh_memory()

@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}

@router.get("/signal-scores")
async def signal_scores_endpoint(
    signal_ids: Optional[str] = None,
    session: Session = Depends(get_session)
) -> dict[str, Any]:
    return get_signal_scores(signal_ids)

def _fetch_verdict_changes(signal_ids: list[str], limit: int, offset: int, session: Session) -> list[VerdictChange]:
    if not signal_ids:
        return []
    try:
        payload = {
            "query": "SELECT signal_id, verdict, previous_verdict, timestamp FROM verdict_changes WHERE signal_id IN (:ids) ORDER BY timestamp DESC LIMIT :limit OFFSET :offset",
            "params": {"ids": signal_ids, "limit": limit, "offset": offset}
        }
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [VerdictChange(**row) for row in results]
    except requests.RequestException:
        return []

def get_signal_scores(signal_ids: Optional[str] = None) -> dict[str, Any]:
    try:
        payload = {"query": "SELECT * FROM mcp_signal_scores LIMIT 100"}
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=30)
        resp.raise_for_status()
        return {"scores": resp.json().get("results", [])}
    except requests.RequestException:
        return {"scores": []}

def get_mesh_memory() -> dict[str, Any]:
    try:
        payload = {"query": "SELECT * FROM mesh_memory LIMIT 100"}
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=30)
        resp.raise_for_status()
        return {"memory": resp.json().get("results", [])}
    except requests.RequestException:
        return {"memory": []}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Verdict Change Feed Service starting")
    yield
    logger.info("Verdict Change Feed Service shutting down")

app = FastAPI(lifespan=lifespan)
app.include_router(router)

def _run_self_test() -> bool:
    try:
        import asyncio
        from fastapi.testclient import TestClient
        from app.db import get_session
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE verdict_changes (signal_id TEXT, verdict TEXT, previous_verdict TEXT, timestamp TEXT)"))
            conn.execute(text("INSERT INTO verdict_changes VALUES ('sig1', 'VULNERABLE', 'UNKNOWN', '2024-01-01')"))
            conn.commit()

        TestingSessionLocal = sessionmaker(bind=engine)

        def override_get_session():
            session = TestingSessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_session] = override_get_session
        client = TestClient(app)

        response = client.get("/verdict-change-feed?signal_ids=sig1")
        if response.status_code != 200:
            logger.error(f"Self-test failed: status {response.status_code}")
            return False

        mesh_resp = client.get("/mesh-scores")
        if mesh_resp.status_code != 200:
            logger.error(f"Mesh scores endpoint failed: {mesh_resp.status_code}")
            return False

        memory_resp = client.get("/mesh-memory")
        if memory_resp.status_code != 200:
            logger.error(f"Mesh memory endpoint failed: {memory_resp.status_code}")
            return False

        health_resp = client.get("/health")
        if health_resp.status_code != 200:
            logger.error(f"Health check failed: {health_resp.status_code}")
            return False

        app.dependency_overrides.clear()
        logger.info("PASS")
        return True
    except Exception as e:
        logger.error(f"Self-test exception: {e}")
        return False

if __name__ == "__main__":
    _run_self_test()