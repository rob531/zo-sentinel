"""
services/staged/definition_history/logic.py

Provides the business logic for the `/api/history` endpoint.
"""

from datetime import datetime
from typing import List, Dict

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, asc

# Application data layer imports (must remain unchanged)
from app.db import get_session
from app.models import McpDefinitionHistory


def _format_entry(entry: McpDefinitionHistory) -> Dict[str, str]:
    """
    Convert a `McpDefinitionHistory` ORM instance into the API representation.

    Returns
    -------
    dict
        ``{"date": <ISO datetime>, "change": <change description>}``
    """
    # The model is expected to have a datetime column named `timestamp`
    # and a textual column named `change`.  If the column name differs,
    # adjust the attribute access accordingly.
    ts = getattr(entry, "timestamp", None) or getattr(entry, "date", None)
    change = getattr(entry, "change", None) or getattr(entry, "description", None)

    # Defensive fallback – should never happen with correct models.
    if ts is None or change is None:
        raise AttributeError("McpDefinitionHistory missing required fields")

    # Ensure ISO‑8601 formatting
    if isinstance(ts, datetime):
        iso_date = ts.isoformat()
    else:
        iso_date = str(ts)

    return {"date": iso_date, "change": str(change)}


def get_definition_history(
    server: int,
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    """
    Retrieve the definition‑change timeline for a given server.

    Parameters
    ----------
    server: int
        The identifier of the server whose history is requested.
    session: Session
        SQLAlchemy session injected by FastAPI's dependency system.

    Returns
    -------
    dict
        ``{"server": <server>, "timeline": [ {"date": ..., "change": ...}, ... ]}``
    """
    stmt = (
        select(McpDefinitionHistory)
        .where(getattr(McpDefinitionHistory, "server", None) == server)
        .order_by(asc(getattr(McpDefinitionHistory, "timestamp", None)))
    )
    rows: List[McpDefinitionHistory] = session.execute(stmt).scalars().all()
    timeline = [_format_entry(row) for row in rows]
    return {"server": server, "timeline": timeline}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The test creates an in‑memory SQLite database, populates it with
    # minimal data, invokes the service logic, and validates the contract.
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # declarative base containing all models

    # ------------------------------------------------------------------- #
    # 1️⃣  Build a throw‑away SQLite engine and initialise the schema
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # ------------------------------------------------------------------- #
    # 2️⃣  Insert test data directly via raw SQL (bypasses ORM constructors)
    # ------------------------------------------------------------------- #
    test_session = SessionLocal()
    test_session.execute(
        text(
            """
            INSERT INTO mcp_definition_history (server, timestamp, "change")
            VALUES (:server, :ts, :chg)
            """
        ),
        [
            {"server": 1, "ts": datetime(2023, 1, 1, 12, 0, 0), "chg": "Added definition A"},
            {"server": 1, "ts": datetime(2023, 1, 2, 13, 0, 0), "chg": "Updated definition B"},
        ],
    )
    test_session.commit()

    # ------------------------------------------------------------------- #
    # 3️⃣  Invoke the business logic
    # ------------------------------------------------------------------- #
    result = get_definition_history(1, test_session)

    # ------------------------------------------------------------------- #
    # 4️⃣  Validate expectations
    # ------------------------------------------------------------------- #
    assert result["server"] == 1, "Server ID mismatch"
    assert isinstance(result["timeline"], list), "Timeline is not a list"
    assert len(result["timeline"]) == 2, "Expected 2 history entries"
    # Ensure ordering is chronological
    dates = [datetime.fromisoformat(item["date"]) for item in result["timeline"]]
    assert dates == sorted(dates), "Timeline not ordered by date"

    print("PASS")