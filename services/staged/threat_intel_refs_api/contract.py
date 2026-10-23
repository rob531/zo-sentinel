"""Threat Intel Refs API - contract test."""
import sys
from typing import Any

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Import the real data layer
from app.db import get_session
from app.models import Base


class ThreatIntelRefResponse(BaseModel):
    indicator_type: str
    indicator_value: str
    pulse_name: str | None
    source: str | None
    source_url: str | None
    pulse_created: str | None
    is_aggregator: bool | None


def create_app() -> FastAPI:
    app = FastAPI(title="Threat Intel Refs API")

    @app.get("/api/servers/{server_id}/threat-intel", response_model=list[ThreatIntelRefResponse])
    def get_server_threat_intel(server_id: str, db: Session = Depends(get_session)) -> list[dict[str, Any]]:
        """Get threat intelligence references for a server."""
        query = """
            SELECT 
                ti.indicator_type,
                ti.indicator_value,
                ti.pulse_name,
                ti.source,
                ti.source_url,
                ti.pulse_created,
                ti.is_aggregator
            FROM threat_intel_refs ti
            INNER JOIN vuln_links vl ON ti.id = vl.threat_intel_ref_id
            WHERE vl.server_id = :server_id
            ORDER BY ti.pulse_created DESC
        """
        result = db.execute(query, {"server_id": server_id})
        rows = result.fetchall()
        return [
            ThreatIntelRefResponse(
                indicator_type=row.indicator_type or "",
                indicator_value=row.indicator_value or "",
                pulse_name=row.pulse_name,
                source=row.source,
                source_url=row.source_url,
                pulse_created=str(row.pulse_created) if row.pulse_created else None,
                is_aggregator=row.is_aggregator,
            ).model_dump()
            for row in rows
        ]

    return app


def run_self_test() -> bool:
    """Run self-test with in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    # Seed test data
    session = TestingSessionLocal()
    try:
        # Create servers
        session.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id TEXT PRIMARY KEY,
                name TEXT
            )
        """)
        session.execute("INSERT OR IGNORE INTO servers (id, name) VALUES ('server-1', 'Test Server 1')")
        session.execute("INSERT OR IGNORE INTO servers (id, name) VALUES ('server-2', 'Test Server 2')")

        # Create threat_intel_refs table and data
        session.execute("""
            CREATE TABLE IF NOT EXISTS threat_intel_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicator_type TEXT,
                indicator_value TEXT,
                pulse_name TEXT,
                source TEXT,
                source_url TEXT,
                pulse_created TEXT,
                is_aggregator INTEGER
            )
        """)
        session.execute("""
            INSERT INTO threat_intel_refs (indicator_type, indicator_value, pulse_name, source, source_url, pulse_created, is_aggregator)
            VALUES 
                ('ip', '192.168.1.1', 'Pulse Alpha', 'Source A', 'http://source-a.com/pulse/1', '2024-01-15 10:00:00', 0),
                ('domain', 'evil.com', 'Pulse Beta', 'Source B', 'http://source-b.com/pulse/2', '2024-01-14 09:00:00', 1),
                ('hash', 'abc123def456', 'Pulse Gamma', 'Source C', 'http://source-c.com/pulse/3', '2024-01-13 08:00:00', 0),
                ('ip', '10.0.0.1', 'Pulse Delta', 'Source D', 'http://source-d.com/pulse/4', '2024-01-12 07:00:00', 1),
                ('domain', 'malware.net', 'Pulse Epsilon', 'Source E', 'http://source-e.com/pulse/5', '2024-01-11 06:00:00', 0),
                ('hash', 'def789ghi012', 'Pulse Zeta', 'Source F', 'http://source-f.com/pulse/6', '2024-01-10 05:00:00', 1)
        """)

        # Create vuln_links table and data
        session.execute("""
            CREATE TABLE IF NOT EXISTS vuln_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                threat_intel_ref_id INTEGER,
                FOREIGN KEY (server_id) REFERENCES servers(id),
                FOREIGN KEY (threat_intel_ref_id) REFERENCES threat_intel_refs(id)
            )
        """)
        session.execute("""
            INSERT INTO vuln_links (server_id, threat_intel_ref_id) VALUES
                ('server-1', 1), ('server-1', 2), ('server-1', 3),
                ('server-2', 4), ('server-2', 5), ('server-2', 6)
        """)
        session.commit()
    finally:
        session.close()

    # Create app with dependency override
    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    # Test server-1
    response = client.get("/api/servers/server-1/threat-intel")
    if response.status_code != 200:
        print(f"FAIL: Expected 200, got {response.status_code}")
        return False

    data = response.json()
    if not isinstance(data, list):
        print("FAIL: Response is not a list")
        return False

    if len(data) < 3:
        print(f"FAIL: Expected at least 3 results, got {len(data)}")
        return False

    # Verify required fields
    required_fields = {"indicator_type", "indicator_value", "pulse_name", "source", "source_url", "pulse_created", "is_aggregator"}
    first_row = data[0]
    if not required_fields.issubset(first_row.keys()):
        missing = required_fields - set(first_row.keys())
        print(f"FAIL: Missing fields in first row: {missing}")
        return False

    # Test server-2
    response2 = client.get("/api/servers/server-2/threat-intel")
    if response2.status_code != 200:
        print(f"FAIL: Server-2 returned {response2.status_code}")
        return False

    data2 = response2.json()
    if len(data2) < 3:
        print(f"FAIL: Server-2 expected at least 3 results, got {len(data2)}")
        return False

    print("PASS")
    return True


if __name__ == "__main__":
    success = run_self_test()
    sys.exit(0 if success else 1)