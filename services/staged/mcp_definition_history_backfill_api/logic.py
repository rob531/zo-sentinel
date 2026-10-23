"""
Logic for mcp_definition_history_backfill_api.

Backfills mcp_definition_history from mcp_server_registry entries that have
null first_seen timestamps. For each server with null first_seen, creates a
history entry with first_seen=last_seen=created_at.
"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpDefinitionHistory

from .router import BackfillResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill_history(session: Session) -> BackfillResponse:
    """
    Backfill mcp_definition_history from mcp_server_registry.

    Reads servers with null first_seen, creates history entries with
    first_seen=last_seen=created_at, and updates the registry entries.

    Returns:
        BackfillResponse with backfilled_rows, skipped_rows, and errors list.
    """
    backfilled_rows = 0
    skipped_rows = 0
    errors: List[dict] = []

    servers = session.query(McpServerRegistry).filter(
        McpServerRegistry.first_seen.is_(None)
    ).all()

    for server in servers:
        try:
            history = McpDefinitionHistory(
                server_id=server.id,
                first_seen=server.created_at,
                last_seen=server.created_at,
            )
            session.add(history)

            server.first_seen = server.created_at
            server.last_seen = server.created_at

            backfilled_rows += 1
            logger.info(f"Backfilled server_id={server.id}")

        except Exception as e:
            session.rollback()
            errors.append({
                "server_id": server.id if server else None,
                "reason": str(e)
            })
            logger.error(f"Failed to backfill server_id={server.id}: {e}")

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        errors.append({
            "server_id": None,
            "reason": f"commit failed: {e}"
        })
        logger.error(f"Commit failed: {e}")

    return BackfillResponse(
        backfilled_rows=backfilled_rows,
        skipped_rows=skipped_rows,
        errors=errors
    )


if __name__ == "__main__":
    from app.models import Base
    from fastapi import FastAPI
    from pydantic import BaseModel

    class SeedServer(BaseModel):
        id: int
        name: str
        created_at: datetime

    in_memory_url = "sqlite:///:memory:"

    engine = create_engine(
        in_memory_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine
    )

    test_session = TestingSessionLocal()

    server1 = McpServerRegistry(
        id=1,
        name="test-server-alpha",
        created_at=datetime(2024, 1, 15, 10, 30, 0),
        first_seen=None,
        last_seen=None,
    )
    server2 = McpServerRegistry(
        id=2,
        name="test-server-beta",
        created_at=datetime(2024, 2, 20, 14, 45, 0),
        first_seen=None,
        last_seen=None,
    )

    test_session.add(server1)
    test_session.add(server2)
    test_session.commit()

    result = backfill_history(test_session)

    test_session.close()

    assert result.backfilled_rows == 2, f"Expected backfilled_rows=2, got {result.backfilled_rows}"
    assert result.skipped_rows == 0, f"Expected skipped_rows=0, got {result.skipped_rows}"
    assert len(result.errors) == 0, f"Expected no errors, got {result.errors}"

    print("PASS")