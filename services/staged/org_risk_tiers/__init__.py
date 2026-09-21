from typing import Any

import httpx
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPSignalScores, MeshMemory, Org


class MeshMemoryResponse(BaseModel):
    server_id: str
    memory: dict[str, Any]


class SignalScore(BaseModel):
    server_id: str
    score: float
    category: str


def get_mesh_memory(server_id: str, session: Session = Depends(get_session)) -> MeshMemoryResponse | None:
    stmt = text("SELECT server_id, memory FROM mesh_memory WHERE server_id = :server_id")
    result = session.execute(stmt, {"server_id": server_id}).fetchone()
    if result:
        return MeshMemoryResponse(server_id=result[0], memory=result[1])
    return None


def get_signal_scores(server_id: str, session: Session = Depends(get_session)) -> list[SignalScore]:
    scores = session.query(MCPSignalScores).filter(MCPSignalScores.server_id == server_id).all()
    return [SignalScore(server_id=s.server_id, score=s.score, category=s.category) for s in scores]


def get_mesh_scores(org_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = text("""
        SELECT ms.server_id, ms.score, ms.category
        FROM mcp_signal_scores ms
        JOIN McpServerRegistry sr ON ms.server_id = sr.server_id
        WHERE sr.org_id = :org_id
    """)
    result = session.execute(stmt, {"org_id": org_id}).fetchall()
    return [{"server_id": r[0], "score": r[1], "category": r[2]} for r in result]


def reset_server_export_api_quarantine(server_id: str, session: Session = Depends(get_session)) -> bool:
    stmt = text("""
        UPDATE McpServerRegistry
        SET export_api_quarantined = false, updated_at = NOW()
        WHERE server_id = :server_id
    """)
    result = session.execute(stmt, {"server_id": server_id})
    session.commit()
    return result.rowcount > 0


def query_mesh_store(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with httpx.Client(timeout=30.0) as client:
        payload = {"query": query, "params": params or {}}
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        resp.raise_for_status()
        return resp.json().get("results", [])


if __name__ == "__main__":
    from app.db import get_session
    from app.models import MCPSignalScores, MeshMemory, Org

    class DummySession:
        def __init__(self):
            self.query_results: list[Any] = []
            self.execute_count = 0
            self.rowcount = 1

        def query(self, model):
            class MockQuery:
                def __init__(self, parent):
                    self.parent = parent

                def filter(self, *args):
                    return self

                def all(self):
                    return self.parent.query_results

            return MockQuery(self)

        def execute(self, stmt, params=None):
            self.execute_count += 1
            self.params = params
            return self

        def fetchone(self):
            return ("test-server", {"status": "ok"})

        def fetchall(self):
            return [("test-server", 0.95, "security")]

        @property
        def rowcount(self):
            return self._rowcount

        @rowcount.setter
        def rowcount(self, value):
            self._rowcount = value

        def commit(self):
            pass

    dummy_session = DummySession()
    dummy_session.query_results = [
        MCPSignalScores(server_id="test-server", score=0.95, category="security")
    ]

    result = get_mesh_memory("test-server", session=dummy_session)
    assert result is not None, "get_mesh_memory failed"
    assert result.server_id == "test-server", "get_mesh_memory server_id mismatch"

    scores = get_signal_scores("test-server", session=dummy_session)
    assert len(scores) == 1, "get_signal_scores failed"
    assert scores[0].server_id == "test-server", "get_signal_scores server_id mismatch"

    mesh_scores = get_mesh_scores(1, session=dummy_session)
    assert len(mesh_scores) == 1, "get_mesh_scores failed"
    assert mesh_scores[0]["server_id"] == "test-server", "get_mesh_scores server_id mismatch"

    reset_ok = reset_server_export_api_quarantine("test-server", session=dummy_session)
    assert reset_ok is True, "reset_server_export_api_quarantine failed"

    print("PASS")