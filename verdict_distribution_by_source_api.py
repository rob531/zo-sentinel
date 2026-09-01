from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class TierCount(BaseModel):
    tier: str
    count: int

class SourceVerdicts(BaseModel):
    source: str
    total: int
    tiers: Dict[str, int]

class VerdictDistributionResponse(BaseModel):
    sources: List[SourceVerdicts]

@router.get("/verdicts/by-source", response_model=VerdictDistributionResponse)
async def get_verdict_distribution_by_source(db: Session = Depends(get_session)):
    results = db.query(
        MCPServerRegistry.registry_source,
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.server_id).label('count')
    ).group_by(
        MCPServerRegistry.registry_source,
        MCPServerRegistry.risk_tier
    ).all()

    source_dict = {}
    for source, tier, count in results:
        if source not in source_dict:
            source_dict[source] = {'total': 0, 'tiers': {}}
        source_dict[source]['total'] += count
        source_dict[source]['tiers'][tier] = source_dict[source]['tiers'].get(tier, 0) + count

    sources = [
        SourceVerdicts(
            source=source,
            total=data['total'],
            tiers=data['tiers']
        )
        for source, data in source_dict.items()
    ]

    return VerdictDistributionResponse(sources=sources)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry

    Base.metadata.create_all(bind=engine)

    from app.db import get_session
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

    app.dependency_overrides[get_session] = _fu369_session_override

    from main import app
    client = TestClient(app)

    test_data = [
        MCPServerRegistry(
            name=f"server_{i}",
            registry_source=f"source{(i % 3) + 1}",
            risk_tier=["TRUSTED_GENERAL", "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "ENTERPRISE_CONTROLLED"][i % 5]
        )
        for i in range(20)
    ]

    with _fu369_session_override() as session:
        session.add_all(test_data)
        session.commit()

    response = client.get("/verdicts/by-source")
    assert response.status_code == 200
    data = response.json()

    assert len(data["sources"]) == 3
    for source in data["sources"]:
        assert source["total"] > 0
        assert len(source["tiers"]) > 0
        for tier in source["tiers"]:
            assert isinstance(tier, str)
            assert isinstance(source["tiers"][tier], int)

    print("PASS")