"""Server Threat Intelligence Router"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session

router = APIRouter()


class ThreatIndicator(BaseModel):
    type: str
    value: str
    pulse_name: str
    source: str
    fetched_at: str


class ThreatIntelResponse(BaseModel):
    server_id: str
    threat_intel: List[ThreatIndicator]


@router.get("/api/server/{server_id}/threat-intel", response_model=ThreatIntelResponse)
async def get_threat_intel(server_id: str, session=Depends(get_session)):
    query = """
        SELECT indicator_type, indicator_value, pulse_name, source_url, fetched_at
        FROM threat_intel_refs
        WHERE server_id = :server_id
    """
    result = session.execute(query, {"server_id": server_id})
    rows = result.fetchall()

    indicators = [
        ThreatIndicator(
            type=row.indicator_type,
            value=row.indicator_value,
            pulse_name=row.pulse_name,
            source=row.source_url,
            fetched_at=row.fetched_at.isoformat() if isinstance(row.fetched_at, datetime) else str(row.fetched_at)
        )
        for row in rows
    ]

    return ThreatIntelResponse(server_id=server_id, threat_intel=indicators)


if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from app.main import app

    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE threat_intel_refs (
            id INTEGER PRIMARY KEY,
            server_id TEXT,
            indicator_type TEXT,
            indicator_value TEXT,
            pulse_name TEXT,
            source_url TEXT,
            fetched_at TEXT
        )
    """)
    conn.execute("""
        INSERT INTO threat_intel_refs VALUES
        (1, 'srv-123', 'ip', '192.168.1.1', 'Pulse One', 'https://example.com/1', '2024-01-15T10:00:00'),
        (2, 'srv-123', 'domain', 'malicious.com', 'Pulse Two', 'https://example.com/2', '2024-01-16T11:00:00')
    """)
    conn.commit()

    def override_get_session():
        return conn

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/server/srv-123/threat-intel")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "srv-123"
    assert len(data["threat_intel"]) == 2
    assert data["threat_intel"][0]["type"] == "ip"
    assert data["threat_intel"][0]["value"] == "192.168.1.1"
    assert data["threat_intel"][1]["type"] == "domain"
    assert data["threat_intel"][1]["value"] == "malicious.com"

    print("PASS")