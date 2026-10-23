"""Risk tier backfill service logic.

Computes a risk tier for every server in the registry and updates the
`risk_tier` column.  Returns a dict with counts of updated and unchanged rows.
"""

from __future__ import annotations

import asyncio
from typing import Dict

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_session
from app.models import McpServerRegistry


async def backfill_risk_tiers(db: Session = Depends(get_session)) -> Dict[str, int]:
    """Back‑fill the `risk_tier` column for all servers.

    The current implementation assigns a static tier of ``"low"`` to every
    server.  The function counts how many rows were changed versus left
    unchanged and commits the changes.

    Returns
    -------
    dict
        ``{"updated_count": int, "unchanged_count": int}``
    """
    servers = db.query(McpServerRegistry).all()
    updated = 0
    unchanged = 0

    for server in servers:
        new_tier = "low"
        if server.risk_tier != new_tier:
            server.risk_tier = new_tier
            updated += 1
        else:
            unchanged += 1

    db.commit()
    return {"updated_count": updated, "unchanged_count": unchanged}


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # Create an in‑memory SQLite DB that mirrors the real app models.
    TEST_ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    # Initialise schema.
    Base.metadata.create_all(TEST_ENGINE)

    # Seed three servers with a NULL risk_tier.
    with TestSessionLocal() as sess:
        for i in range(3):
            srv = McpServerRegistry(
                server_id=f"server{i}",
                name=f"Server {i}",
                risk_tier=None,
            )
            sess.add(srv)
        sess.commit()

    # Run the backfill logic against the test DB.
    async def _run_test() -> None:
        with TestSessionLocal() as sess:
            result = await backfill_risk_tiers(sess)
            assert result["updated_count"] == 3, (
                f"expected updated_count 3, got {result['updated_count']}"
            )
            assert result["unchanged_count"] == 0, (
                f"expected unchanged_count 0, got {result['unchanged_count']}"
            )
            print("PASS")

    asyncio.run(_run_test())