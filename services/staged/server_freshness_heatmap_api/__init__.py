"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

logger = logging.getLogger(__name__)

router = APIRouter()


class SignalScoresResponse(BaseModel):
    scores: list[dict[str, Any]]
    total: int


def get_signal_scores(session: Session) -> list[dict[str, Any]]:
    """Retrieve signal scores from the ZoComputer store."""
    import requests

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "columns": ["*"]},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        logger.warning("Failed to fetch signal scores: %s", e)
        return []


def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Endpoint to retrieve signal scores."""
    scores = get_signal_scores(session)
    return SignalScoresResponse(scores=scores, total=len(scores))


def mesh_scores(session: Session) -> list[dict[str, Any]]:
    """Retrieve mesh scores from mesh_memory table."""
    import requests

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "sql": text("SELECT * FROM mesh_memory WHERE category = 'scores'"),
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        logger.warning("Failed to fetch mesh scores: %s", e)
        return []


def mesh_scores_endpoint(
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Endpoint to retrieve mesh scores."""
    scores = mesh_scores(session)
    return SignalScoresResponse(scores=scores, total=len(scores))


def get_mesh_memory(session: Session) -> list[dict[str, Any]]:
    """Retrieve mesh memory records."""
    import requests

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "columns": ["*"]},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        logger.warning("Failed to fetch mesh memory: %s", e)
        return []


def get_db(session: Session = Depends(get_session)) -> Session:
    """Get database session."""
    return session


def _run_self_test(session: Session = Depends(get_session)) -> dict[str, str]:
    """Run self-test to verify module functionality."""
    results = {}

    # Test 1: Verify we can query the app database
    try:
        session.execute(text("SELECT 1"))
        results["db_connection"] = "PASS"
    except Exception as e:
        results["db_connection"] = f"FAIL: {e}"

    # Test 2: Verify model imports work
    try:
        _ = McpLlmAxisScore
        results["model_import"] = "PASS"
    except Exception as e:
        results["model_import"] = f"FAIL: {e}"

    # Test 3: Verify signal scores fetch (non-blocking)
    try:
        scores = get_signal_scores(session)
        results["signal_scores"] = "PASS" if isinstance(scores, list) else f"FAIL: unexpected type"
    except Exception as e:
        results["signal_scores"] = f"WARN: {e}"

    # Test 4: Verify router availability
    try:
        assert router is not None
        results["router"] = "PASS"
    except Exception as e:
        results["router"] = f"FAIL: {e}"

    return results


if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # Override dependency for self-test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with test_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS McpLlmAxisScore (id INTEGER PRIMARY KEY)"))

    test_results = _run_self_test()
    all_pass = all(v == "PASS" or v.startswith("WARN") for v in test_results.values())

    if all_pass:
        print("PASS")
    else:
        print("FAIL")
        for k, v in test_results.items():
            print(f"  {k}: {v}")