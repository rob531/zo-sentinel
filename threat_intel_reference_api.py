from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import ThreatIntelRef as DBThreatIntelRef

router = APIRouter()

class ThreatIntelRef(BaseModel):
    indicator_type: str
    indicator_value: str
    pulse_name: str
    source: str
    pulse_created: str
    is_aggregator: bool

class ThreatIntelRefList(BaseModel):
    references: List[ThreatIntelRef]

class ThreatIntelRefDetail(BaseModel):
    reference: ThreatIntelRef

def get_threat_intel_refs(
    session=Depends(get_session),
    source: Optional[str] = None,
    limit: int = 100
) -> List[DBThreatIntelRef]:
    query = session.query(DBThreatIntelRef)
    if source:
        query = query.filter(DBThreatIntelRef.source == source)
    return query.limit(limit).all()

@router.get("/threat-intel/references", response_model=ThreatIntelRefList)
async def list_references(
    source: Optional[str] = Query(None),
    limit: int = Query(100)
):
    refs = get_threat_intel_refs(source=source, limit=limit)
    return {"references": [ThreatIntelRef(**ref.__dict__) for ref in refs]}

@router.get("/threat-intel/references/{indicator_type}/{indicator_value}", response_model=ThreatIntelRefDetail)
async def get_reference(
    indicator_type: str,
    indicator_value: str,
    session=Depends(get_session)
):
    ref = session.query(DBThreatIntelRef).filter(
        DBThreatIntelRef.indicator_type == indicator_type,
        DBThreatIntelRef.indicator_value == indicator_value
    ).first()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference not found")
    return {"reference": ThreatIntelRef(**ref.__dict__)}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import ThreatIntelRef
    # FU-369: `app.dependency_overrides` is not a module in this repo, so the import
    # that stood here raised ModuleNotFoundError the moment this block ran. The
    # override is defined locally instead, per the pattern in
    # services/active/cadence_job_sla_report/contract.py.
    from sqlalchemy import create_engine as _fu369_create_engine
    from sqlalchemy.orm import sessionmaker as _fu369_sessionmaker

    _FU369Session = _fu369_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_fu369_create_engine("sqlite:///:memory:"),
    )


    def _fu369_session_override(session_factory=None):
        """Test session override covering every call shape used in this repo.

        Called with a sessionmaker it returns a dependency callable bound to that
        factory; called with nothing it returns a Session, which is what a FastAPI
        dependency override needs AND what `with ... as session:` needs, because
        Session implements the context-manager protocol itself.
        """
        if session_factory is not None:
            return lambda: session_factory()
        return _FU369Session()

    # Setup in-memory test database
    Base.metadata.create_all(bind=engine)
    _fu369_session_override()

    # Seed test data
    test_data = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="8.8.8.8",
            pulse_name="Test Pulse 1",
            source="OTX",
            pulse_created="2023-01-01",
            is_aggregator=False
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.com",
            pulse_name="Test Pulse 2",
            source="MISP",
            pulse_created="2023-01-02",
            is_aggregator=True
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="1.1.1.1",
            pulse_name="Test Pulse 3",
            source="OTX",
            pulse_created="2023-01-03",
            is_aggregator=False
        )
    ]
    session = _fu369_session_override()
    for data in test_data:
        session.add(data)
    session.commit()

    # Create test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test endpoints
    response = client.get("/threat-intel/references")
    assert response.status_code == 200
    assert len(response.json()["references"]) == 3

    response = client.get("/threat-intel/references/ip/8.8.8.8")
    assert response.status_code == 200
    assert response.json()["reference"]["indicator_value"] == "8.8.8.8"

    response = client.get("/threat-intel/references?source=OTX")
    assert response.status_code == 200
    assert len(response.json()["references"]) == 2

    print("PASS")