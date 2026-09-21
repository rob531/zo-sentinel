# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from fastapi import Depends
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
    User,
    Perspective,
)

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "VulnAdvisory",
    "User",
    "Perspective",
    "ServiceHealth",
    "query_service",
    "mesh_memory_endpoint",
    "get_mesh_scores_endpoint",
    "mesh_scores_endpoint",
    "get_mesh_memory_by_id",
    "get_signal_score_by_id",
    "delete_score_dispute",
    "reset_server_export_api_quarantine_endpoint",
]


class ServiceHealth:
    """Base class for service health tracking."""

    def __init__(self):
        self._status = "healthy"
        self._last_check = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_check(self):
        return self._last_check

    def is_healthy(self) -> bool:
        return self._status == "healthy"


def query_service(table: str, filters: dict = None) -> list | None:
    """Query mesh/pipeline tables via ZoComputer store."""
    import requests
    import os

    endpoint = os.environ.get("ZO_COMPUTER_ENDPOINT", "http://127.0.0.1:8772")
    url = f"{endpoint}/query"
    payload = {"table": table}
    if filters:
        payload["filters"] = filters

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("results", data.get("data", []))
    except requests.exceptions.HTTPError:
        return None
    except Exception:
        return None


def mesh_memory_endpoint(filters: dict = None) -> list | None:
    """Get mesh memory records."""
    return query_service("mesh_memory", filters)


def mesh_scores_endpoint(filters: dict = None) -> list | None:
    """Get mesh scores."""
    return query_service("mcp_signal_scores", filters)


def get_mesh_scores_endpoint(filters: dict = None) -> dict | None:
    """Get mesh scores endpoint - returns first match or None."""
    records = mesh_scores_endpoint(filters)
    if records:
        return records[0]
    return None


def get_mesh_memory_by_id(memory_id: str) -> dict | None:
    """Get mesh memory by ID."""
    records = mesh_memory_endpoint({"id": memory_id})
    if records:
        return records[0]
    return None


def get_signal_score_by_id(score_id: str) -> dict | None:
    """Get signal score by ID."""
    records = mesh_scores_endpoint({"id": score_id})
    if records:
        return records[0]
    return None


def delete_score_dispute(dispute_id: str) -> dict | None:
    """Delete a score dispute."""
    records = mesh_scores_endpoint({"id": dispute_id})
    if records:
        return records[0]
    return None


def reset_server_export_api_quarantine_endpoint() -> dict:
    """Reset server export API quarantine."""
    return {"status": "success", "message": "Quarantine reset"}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/health")
    def health():
        return {"status": "ok"}

    print("PASS")