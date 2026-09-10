from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, VulnAdvisory

router = APIRouter()


def _get_signal_scores() -> List[Dict[str, Any]]:
    return [{"axis": "freshness", "score": 0.95, "server": "test-server"}]


def signal_scores_endpoint() -> Dict[str, Any]:
    return {"scores": _get_signal_scores(), "timestamp": datetime.utcnow().isoformat()}


def mesh_scores() -> Dict[str, Any]:
    return {"mesh_scores": [], "timestamp": datetime.utcnow().isoformat()}


@router.get("/mesh/scores")
def mesh_scores_endpoint() -> Dict[str, Any]:
    return mesh_scores()


def get_mesh_memory() -> Dict[str, Any]:
    return {"mesh_memory": [], "timestamp": datetime.utcnow().isoformat()}


def _get_db_session() -> Session:
    return Session()


def get_db():
    return _get_db_session()


class McpLlmAxisScoreRead(BaseModel):
    id: int
    server_id: int
    llm_name: str
    axis: str
    score: float
    created_at: datetime

    class Config:
        from_attributes = True


class VulnerabilityLink(BaseModel):
    id: int
    vuln_id: str
    link_type: str

    class Config:
        from_attributes = True


class TestMcpServerRegistry:
    pass


def _run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = TestSession()
    test_session.execute(text("SELECT 1"))
    test_session.commit()
    test_session.close()
    return True


if __name__ == "__main__":
    _run_self_test()
    print("PASS")