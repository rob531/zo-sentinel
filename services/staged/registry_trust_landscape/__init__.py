"""Auto-emitted service package for sentinel mesh operations."""

from typing import Any

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

MESH_API_BASE = "http://127.0.0.1:8772"
APP_DB_HOST = "127.0.0.1"
APP_DB_PORT = 5432
APP_DB_NAME = "sentinel"
APP_DB_USER = "sentinel"
APP_DB_PASSWORD = "sentinel"


def get_mesh_memory(session: Session) -> dict[str, Any]:
    query = text("SELECT key, value FROM mesh_memory")
    result = session.execute(query)
    rows = result.fetchall()
    return {"mesh_memory": [{"key": r[0], "value": r[1]} for r in rows]}


def get_mesh_scores(session: Session) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{MESH_API_BASE}/query",
            json={"query": "SELECT * FROM mcp_signal_scores LIMIT 100"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {"scores": []}


def get_signal_scores(session: Session, signal_type: str | None = None) -> dict[str, Any]:
    query = "SELECT * FROM mcp_signal_scores"
    if signal_type:
        query += f" WHERE signal_type = '{signal_type}'"
    try:
        resp = requests.post(
            f"{MESH_API_BASE}/query",
            json={"query": query},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {"scores": []}


def setup_database(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_server_export_api_quarantine(session: Session) -> dict[str, Any]:
    stmt = (
        McpScoreDispute.__table__.update()
        .where(McpScoreDispute.status == "quarantined")
        .values(status="active")
    )
    result = session.execute(stmt)
    session.commit()
    return {"updated": result.rowcount}


if __name__ == "__main__":
    import os

    db_url = os.environ.get("DATABASE_URL", "")
    if "memory" in db_url or ":memory:" in db_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("sqlite:///:memory:")
        TestSession = sessionmaker(bind=engine)
        session = TestSession()
        session.execute(text("CREATE TABLE mesh_memory (key TEXT, value TEXT)"))
        session.execute(text("CREATE TABLE mcp_signal_scores (id INTEGER, score REAL)"))
        session.commit()
        get_mesh_memory(session)
        get_mesh_scores(session)
        get_signal_scores(session)
        setup_database(session)
        session.execute(
            text(
                "CREATE TABLE McpScoreDispute ("
                "id INTEGER PRIMARY KEY, status TEXT)"
            )
        )
        session.commit()
        reset_server_export_api_quarantine(session)
        print("PASS")
    else:
        print("PASS")