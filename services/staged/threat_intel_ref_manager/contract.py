from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session

router = APIRouter(prefix="", tags=["threat_intel_refs"])


class ThreatIntelRefResponse(BaseModel):
    ref_id: int
    indicator_type: str
    indicator_value: str
    pulse_id: Optional[str] = None
    pulse_name: Optional[str] = None
    pulse_created: Optional[datetime] = None
    is_aggregator: bool = False
    source: Optional[str] = None
    source_url: Optional[str] = None
    fetched_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/threat/intel/refs", response_model=list[ThreatIntelRefResponse])
def get_threat_intel_refs(session: Session = Depends(get_session)):
    query = text("""
        SELECT ref_id, indicator_type, indicator_value, pulse_id, pulse_name,
               pulse_created, is_aggregator, source, source_url, fetched_at
        FROM threat_intel_refs
        ORDER BY ref_id
    """)
    result = session.execute(query)
    rows = result.fetchall()
    return [
        ThreatIntelRefResponse(
            ref_id=row[0],
            indicator_type=row[1],
            indicator_value=row[2],
            pulse_id=row[3],
            pulse_name=row[4],
            pulse_created=row[5],
            is_aggregator=bool(row[6]) if row[6] is not None else False,
            source=row[7],
            source_url=row[8],
            fetched_at=row[9],
        )
        for row in rows
    ]


@router.get("/threat/intel/refs/{ref_id}", response_model=ThreatIntelRefResponse)
def get_threat_intel_ref(ref_id: int, session: Session = Depends(get_session)):
    query = text("""
        SELECT ref_id, indicator_type, indicator_value, pulse_id, pulse_name,
               pulse_created, is_aggregator, source, source_url, fetched_at
        FROM threat_intel_refs
        WHERE ref_id = :ref_id
    """)
    result = session.execute(query, {"ref_id": ref_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Threat intel ref not found")
    return ThreatIntelRefResponse(
        ref_id=row[0],
        indicator_type=row[1],
        indicator_value=row[2],
        pulse_id=row[3],
        pulse_name=row[4],
        pulse_created=row[5],
        is_aggregator=bool(row[6]) if row[6] is not None else False,
        source=row[7],
        source_url=row[8],
        fetched_at=row[9],
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    with test_engine.connect() as conn:
        conn.execute(
            text(
                """CREATE TABLE threat_intel_refs (
                    ref_id INTEGER PRIMARY KEY,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    pulse_created TIMESTAMP,
                    is_aggregator INTEGER DEFAULT 0,
                    source TEXT,
                    source_url TEXT,
                    fetched_at TIMESTAMP
                )"""
            )
        )
        conn.execute(
            text(
                """INSERT INTO threat_intel_refs
                   (ref_id, indicator_type, indicator_value, pulse_id, pulse_name,
                    pulse_created, is_aggregator, source, source_url, fetched_at)
                   VALUES
                   (1, 'ip', '192.168.1.1', 'PLS-001', 'Test Pulse',
                    '2024-01-15 10:30:00', 1, 'AlienVault', 'https://otx.alienvault.com/pulse/1',
                    '2024-01-15 12:00:00'),
                   (2, 'domain', 'malware.bad.com', 'PLS-002', 'Malware Domain',
                    '2024-01-14 08:00:00', 0, 'ThreatFox', 'https://threatfox.abuse.ch',
                    '2024-01-14 09:00:00')"""
            )
        )
        conn.commit()

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    response = client.get("/threat/intel/refs")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    refs = response.json()
    assert len(refs) == 2, f"Expected 2 refs, got {len(refs)}"
    assert refs[0]["ref_id"] == 1
    assert refs[0]["indicator_type"] == "ip"
    assert refs[0]["indicator_value"] == "192.168.1.1"
    assert refs[0]["pulse_name"] == "Test Pulse"
    assert refs[0]["is_aggregator"] is True
    assert refs[0]["source"] == "AlienVault"

    single = client.get("/threat/intel/refs/1")
    assert single.status_code == 200
    data = single.json()
    assert data["ref_id"] == 1
    assert data["indicator_type"] == "ip"
    assert data["indicator_value"] == "192.168.1.1"

    not_found = client.get("/threat/intel/refs/999")
    assert not_found.status_code == 404

    print("PASS")