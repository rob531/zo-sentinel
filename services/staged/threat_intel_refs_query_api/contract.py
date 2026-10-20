from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, Query
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db import get_session
from app.models import ThreatIntelRef


def query_refs(
    session: Session,
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[datetime] = None,
) -> list[dict]:
    stmt = session.query(ThreatIntelRef)
    params = {}
    if indicator_type:
        stmt = stmt.where(ThreatIntelRef.indicator_type == indicator_type)
    if source:
        stmt = stmt.where(ThreatIntelRef.source == source)
    if since:
        stmt = stmt.where(ThreatIntelRef.fetched_at >= since)
    for row in stmt.all():
        yield {
            "indicator_type": row.indicator_type,
            "indicator_value": row.indicator_value,
            "pulse_id": row.pulse_id,
            "pulse_name": row.pulse_name,
            "pulse_created": row.pulse_created,
            "source": row.source,
            "source_url": row.source_url,
            "fetched_at": row.fetched_at,
        }


app = FastAPI()


@app.get("/api/threat-intel/refs")
def get_refs(
    indicator_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    refs = list(query_refs(session, indicator_type, source, since))
    return {"refs": refs}


def _run_self_test() -> None:
    refs = list(query_refs(get_session()))
    assert isinstance(refs, list)


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    ThreatIntelRef.metadata.create_all(engine)

    def override_get_session() -> Session:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(app.router)

    with TestingSessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="1.2.3.4",
            pulse_id=1,
            pulse_name="Pulse One",
            pulse_created=now,
            source="source_a",
            source_url="http://example.com/1",
            fetched_at=now,
        ))
        session.add(ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="5.6.7.8",
            pulse_id=2,
            pulse_name="Pulse Two",
            pulse_created=now,
            source="source_b",
            source_url="http://example.com/2",
            fetched_at=now,
        ))
        session.add(ThreatIntelRef(
            indicator_type="domain",
            indicator_value="evil.com",
            pulse_id=3,
            pulse_name="Pulse Three",
            pulse_created=now,
            source="source_a",
            source_url="http://example.com/3",
            fetched_at=now,
        ))
        session.commit()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/refs")
        assert response.status_code == 200
        data = response.json()
        assert "refs" in data
        refs = data["refs"]
        assert len(refs) >= 1
        matching = [r for r in refs if r["indicator_type"] == "ipv4"]
        assert len(matching) >= 1

    print("PASS")