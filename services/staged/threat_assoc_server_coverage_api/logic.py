"""Logic for the Threat Association Server Coverage API."""

from typing import List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class TopServer(BaseModel):
    server_id: str
    assoc_count: int


class CoverageResponse(BaseModel):
    registered_servers: int
    servers_with_assoc: int
    servers_without_assoc: int
    coverage_pct: float
    top_servers: List[TopServer]


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #


def get_coverage(session: Session = Depends(get_session)) -> CoverageResponse:
    """
    Compute server coverage statistics based on threat associations.

    Returns a ``CoverageResponse`` containing:
        * total number of registered servers
        * number of servers that have at least one threat association
        * number of servers without any threat association
        * coverage percentage (rounded to 2 decimals)
        * list of servers ordered by descending association count
    """
    # total registered servers
    registered_stmt = text("SELECT COUNT(*) FROM mcp_server_registry")
    registered = session.execute(registered_stmt).scalar_one()

    # servers with at least one association (distinct)
    with_assoc_stmt = text(
        """
        SELECT COUNT(DISTINCT ta.server_id)
        FROM mcp_threat_associations AS ta
        JOIN mcp_server_registry AS sr
          ON ta.server_id = sr.server_id
        """
    )
    servers_with = session.execute(with_assoc_stmt).scalar_one()

    # servers without any association
    servers_without = registered - servers_with

    # coverage percentage
    coverage_pct = (
        round((servers_with / registered) * 100.0, 2) if registered else 0.0
    )

    # top servers by association count
    top_stmt = text(
        """
        SELECT ta.server_id, COUNT(*) AS assoc_count
        FROM mcp_threat_associations AS ta
        JOIN mcp_server_registry AS sr
          ON ta.server_id = sr.server_id
        GROUP BY ta.server_id
        ORDER BY assoc_count DESC
        """
    )
    top_rows = session.execute(top_stmt).fetchall()
    top_servers = [
        TopServer(server_id=row[0], assoc_count=row[1]) for row in top_rows
    ]

    return CoverageResponse(
        registered_servers=registered,
        servers_with_assoc=servers_with,
        servers_without_assoc=servers_without,
        coverage_pct=coverage_pct,
        top_servers=top_servers,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #


def _self_test() -> None:
    """Run a minimal in‑memory test to verify the logic."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create minimal schema required for the test
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    confidence INTEGER,
                    description TEXT,
                    first_seen TEXT,
                    last_assessed TEXT,
                    last_scanned TEXT,
                    last_seen TEXT,
                    meta TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    trust_score INTEGER,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    severity TEXT
                )
                """
            )
        )

        # Seed four registered servers
        conn.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id) VALUES
                ('s1'), ('s2'), ('s3'), ('s4')
                """
            )
        )

        # Seed three threat‑association rows (two for s1, one for s2)
        conn.execute(
            text(
                """
                INSERT INTO mcp_threat_associations (server_id, severity) VALUES
                ('s1', 'high'),
                ('s1', 'medium'),
                ('s2', 'low')
                """
            )
        )

    # Execute the logic against the test DB
    with SessionLocal() as session:
        result = get_coverage(session)

    # Assertions matching the contract
    assert result.registered_servers == 4
    assert result.servers_with_assoc == 2
    assert result.servers_without_assoc == 2
    # Top server should be s1 with two associations
    top_match = any(
        ts.server_id == "s1" and ts.assoc_count == 2 for ts in result.top_servers
    )
    assert top_match

    print("PASS")


if __name__ == "__main__":
    _self_test()