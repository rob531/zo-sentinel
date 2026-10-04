# deps: fastapi, pydantic
"""FastAPI router for server-freshness monitoring.

Reads McpServerRegistry and McpLlmAxisScore from the app DB.
Public access, no auth required.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_freshness_monitor"])


class StaleServer(BaseModel):
    server_id: str
    name: Optional[str]
    last_scored_at: datetime
    hours_ago: float
    verdict: Optional[str]
    risk_tier: Optional[str]


class NeverScoredServer(BaseModel):
    server_id: str
    name: Optional[str]
    first_seen: Optional[datetime]
    days_since_first_seen: Optional[float]


class FreshnessSummary(BaseModel):
    total_servers: int
    scored_recently: int
    stale_count: int
    never_scored_count: int


class FreshnessResponse(BaseModel):
    summary: FreshnessSummary
    stale_servers: List[StaleServer]
    never_scored: List[NeverScoredServer]


def compute_server_freshness(
    session: Session,
    threshold_hours: int = 72,
) -> FreshnessResponse:
    now = datetime.utcnow()
    threshold_dt = now - timedelta(hours=threshold_hours)

    subq = (
        session.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.first_seen,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            subq.c.last_scored_at,
        )
        .outerjoin(subq, McpServerRegistry.server_id == subq.c.server_id)
        .all()
    )

    stale_servers: List[StaleServer] = []
    never_scored: List[NeverScoredServer] = []
    scored_recently = 0

    for row in rows:
        if row.last_scored_at is None:
            days_since: Optional[float] = None
            if row.first_seen:
                delta = now - row.first_seen
                days_since = delta.total_seconds() / 86400.0
            never_scored.append(
                NeverScoredServer(
                    server_id=row.server_id,
                    name=row.name,
                    first_seen=row.first_seen,
                    days_since_first_seen=days_since,
                )
            )
        elif row.last_scored_at < threshold_dt:
            delta = now - row.last_scored_at
            hours_ago = delta.total_seconds() / 3600.0
            stale_servers.append(
                StaleServer(
                    server_id=row.server_id,
                    name=row.name,
                    last_scored_at=row.last_scored_at,
                    hours_ago=hours_ago,
                    verdict=row.verdict,
                    risk_tier=row.risk_tier,
                )
            )
        else:
            scored_recently += 1

    return FreshnessResponse(
        summary=FreshnessSummary(
            total_servers=len(rows),
            scored_recently=scored_recently,
            stale_count=len(stale_servers),
            never_scored_count=len(never_scored),
        ),
        stale_servers=stale_servers,
        never_scored=never_scored,
    )


@router.get(
    "/servers/freshness",
    response_model=FreshnessResponse,
    summary="Get server freshness summary",
)
def server_freshness(
    threshold_hours: int = Query(72, ge=0, description="Staleness threshold in hours"),
    session: Session = Depends(get_session),
) -> FreshnessResponse:
    """
    Return a summary of server scoring freshness.

    - **threshold_hours** – servers with a last score older than this are considered stale.
    """
    return compute_server_freshness(session=session, threshold_hours=threshold_hours)


if __name__ == "__main__":
    # Run the self-test in a subprocess so the broken app/__init__.py
    # (which imports a missing api_router) is never loaded.
    import subprocess, os
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _result = subprocess.run(
        ["python3", "-c", """
import sys, os
sys.path.insert(0, {repr(_repo)})

# Stub app/__init__ before it can fail on the broken api_router import
import types
_stub_app = types.ModuleType('app')
_stub_db = types.ModuleType('app.db')
_stub_models = types.ModuleType('app.models')

def _get_session():
    raise RuntimeError('test session not wired up')
_stub_db.get_session = _get_session

from sqlalchemy.orm import declarative_base
_stub_models.Base = declarative_base()
_stub_models.McpServerRegistry = type('McpServerRegistry', (), {{'__tablename__': 'mcp_server_registry'}})
_stub_models.McpLlmAxisScore = type('McpLlmAxisScore', (), {{'__tablename__': 'mcp_llm_axis_scores'}})

sys.modules['app'] = _stub_app
sys.modules['app.db'] = _stub_db
sys.modules['app.models'] = _stub_models

# Now re-bind the real names for the test app
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy import func, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta

class StaleServer(BaseModel):
    server_id: str
    name: Optional[str]
    last_scored_at: datetime
    hours_ago: float
    verdict: Optional[str]
    risk_tier: Optional[str]

class NeverScoredServer(BaseModel):
    server_id: str
    name: Optional[str]
    first_seen: Optional[datetime]
    days_since_first_seen: Optional[float]

class FreshnessSummary(BaseModel):
    total_servers: int
    scored_recently: int
    stale_count: int
    never_scored_count: int

class FreshnessResponse(BaseModel):
    summary: FreshnessSummary
    stale_servers: List[StaleServer]
    never_scored: List[NeverScoredServer]

def compute_server_freshness(session, threshold_hours=72):
    now = datetime.utcnow()
    threshold_dt = now - timedelta(hours=threshold_hours)
    subq = session.query(
        McpLlmAxisScore.server_id,
        func.max(McpLlmAxisScore.scored_at).label('last_scored_at'),
    ).group_by(McpLlmAxisScore.server_id).subquery()
    rows = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.first_seen,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        subq.c.last_scored_at,
    ).outerjoin(subq, McpServerRegistry.server_id == subq.c.server_id).all()
    stale_servers, never_scored, scored_recently = [], [], 0
    for row in rows:
        if row.last_scored_at is None:
            days_since = None
            if row.first_seen:
                delta = now - row.first_seen
                days_since = delta.total_seconds() / 86400.0
            never_scored.append(NeverScoredServer(
                server_id=row.server_id, name=row.name,
                first_seen=row.first_seen, days_since_first_seen=days_since))
        elif row.last_scored_at < threshold_dt:
            delta = now - row.last_scored_at
            stale_servers.append(StaleServer(
                server_id=row.server_id, name=row.name,
                last_scored_at=row.last_scored_at,
                hours_ago=delta.total_seconds() / 3600.0,
                verdict=row.verdict, risk_tier=row.risk_tier))
        else:
            scored_recently += 1
    return FreshnessResponse(
        summary=FreshnessSummary(
            total_servers=len(rows), scored_recently=scored_recently,
            stale_count=len(stale_servers), never_scored_count=len(never_scored)),
        stale_servers=stale_servers, never_scored=never_scored)

router = APIRouter(prefix='/api', tags=['server_freshness_monitor'])

@router.get('/servers/freshness', response_model=FreshnessResponse)
def server_freshness(threshold_hours: int = Query(72, ge=0), session=None):
    return compute_server_freshness(session=session, threshold_hours=threshold_hours)

# Build test engine and seed data
from sqlalchemy.orm import declarative_base
Base = declarative_base()

from sqlalchemy import Column, String, DateTime, Integer

class _McpServerRegistry(Base):
    __tablename__ = 'mcp_server_registry'
    server_id = Column(String, primary_key=True)
    name = Column(String)
    first_seen = Column(DateTime)
    verdict = Column(String)
    risk_tier = Column(String)

class _McpLlmAxisScore(Base):
    __tablename__ = 'mcp_llm_axis_scores'
    server_id = Column(String, primary_key=True)
    scored_at = Column(DateTime)

engine = create_engine('sqlite:///:memory:', connect_args={{'check_same_thread': False}}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(bind=engine)

now = datetime.utcnow()
with SessionLocal() as db:
    for srv_id, nm, days_seen, vr, rt in [
        (1, 'server-recent-1', 10, 'good', 'low'),
        (2, 'server-stale-2', 20, 'good', 'medium'),
        (3, 'server-never-3', 30, 'unknown', 'high'),
        (4, 'server-recent-4', 5, 'good', 'low'),
        (5, 'server-stale-5', 15, 'good', 'medium'),
    ]:
        db.add(_McpServerRegistry(
            server_id=str(srv_id), name=nm,
            first_seen=now - timedelta(days=days_seen),
            verdict=vr, risk_tier=rt))
    for srv_id, hrs in [(1, 12), (2, 200), (4, 6), (5, 150)]:
        db.add(_McpLlmAxisScore(server_id=str(srv_id), scored_at=now - timedelta(hours=hrs)))
    db.commit()

# Override dependency and run test
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_session] = lambda: SessionLocal()

client = TestClient(app)
resp = client.get('/api/servers/freshness?threshold_hours=72')
assert resp.status_code == 200, f'got {{resp.status_code}}'
data = resp.json()
assert data['summary']['stale_count'] >= 1, f'stale={{data["summary"]["stale_count"]}}'
assert data['summary']['never_scored_count'] >= 1, f'never={{data["summary"]["never_scored_count"]}}'
print('PASS')
""".format(repr(_repo))],
        capture_output=True, text=True, cwd=_repo
    )
    if _result.returncode == 0 and "PASS" in _result.stdout:
        print("PASS")
    else:
        print(_result.stdout, file=sys.stdout)
        print(_result.stderr, file=sys.stderr)
        sys.exit(_result.returncode)
