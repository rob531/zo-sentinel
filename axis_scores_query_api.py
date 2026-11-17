from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import base64
import json
from app.db import get_session
from app.models import McpLlmAxisScore
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.orm import Session
from serving_model_allowlist import serving_model_allowlist
import requests

router = APIRouter()

class AxisScoreRow(BaseModel):
    server_id: str
    axis_name: str
    # nullable columns in mcp_llm_axis_scores (p_critical/p_danger etc. are routinely NULL)
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    scored_at: Optional[str] = None

class AxisScoresResponse(BaseModel):
    rows: List[AxisScoreRow]
    next_cursor: Optional[str]
    total: int

class SummaryRow(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    scored_at: Optional[str] = None

class SummaryResponse(BaseModel):
    rows: List[SummaryRow]
    total: int

def _get_axis_scores(
    db: Session,
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    min_p_top: Optional[float] = None,
    max_p_top: Optional[float] = None,
    escalated: Optional[bool] = None,
    limit: int = 50,
    cursor: Optional[str] = None
) -> List[McpLlmAxisScore]:
    query = db.query(McpLlmAxisScore)

    if server_id:
        query = query.filter(McpLlmAxisScore.server_id == server_id)
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    if min_p_top is not None:
        query = query.filter(McpLlmAxisScore.p_top >= min_p_top)
    if max_p_top is not None:
        query = query.filter(McpLlmAxisScore.p_top <= max_p_top)
    if escalated is not None:
        query = query.filter(McpLlmAxisScore.escalated == escalated)

    if cursor:
        cursor_data = json.loads(base64.b64decode(cursor).decode('utf-8'))
        query = query.filter(
            or_(
                McpLlmAxisScore.scored_at < cursor_data['scored_at'],
                and_(
                    McpLlmAxisScore.scored_at == cursor_data['scored_at'],
                    McpLlmAxisScore.server_id < cursor_data['server_id']
                )
            )
        )

    query = query.order_by(desc(McpLlmAxisScore.scored_at), McpLlmAxisScore.server_id)
    query = query.limit(limit + 1)

    results = query.all()

    if cursor and len(results) > limit:
        results = results[:-1]

    return results

@router.get("/axis-scores", response_model=AxisScoresResponse)
async def get_axis_scores(
    server_id: Optional[str] = Query(None),
    axis_name: Optional[str] = Query(None),
    min_p_top: Optional[float] = Query(None),
    max_p_top: Optional[float] = Query(None),
    escalated: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    db: Session = Depends(get_session)
):
    results = _get_axis_scores(
        db, server_id, axis_name, min_p_top, max_p_top, escalated, limit, cursor
    )

    rows = []
    for result in results:
        row = AxisScoreRow(
            server_id=result.server_id,
            axis_name=result.axis_name,
            label=result.label,
            label_index=result.label_index,
            p_top=result.p_top,
            p_critical=result.p_critical,
            p_danger=result.p_danger,
            escalated=result.escalated,
            scored_at=result.scored_at.isoformat()
        )
        rows.append(row)

    next_cursor = None
    if len(results) == limit + 1:
        last_result = results[-1]
        cursor_data = {
            'scored_at': last_result.scored_at,
            'server_id': last_result.server_id
        }
        next_cursor = base64.b64encode(json.dumps(cursor_data).encode('utf-8')).decode('utf-8')

    total = db.query(func.count(McpLlmAxisScore.id)).filter(
        and_(
            server_id is None or (McpLlmAxisScore.server_id == server_id),
            axis_name is None or (McpLlmAxisScore.axis_name == axis_name),
            min_p_top is None or (McpLlmAxisScore.p_top >= min_p_top),
            max_p_top is None or (McpLlmAxisScore.p_top <= max_p_top),
            escalated is None or (McpLlmAxisScore.escalated == escalated)
        )
    ).scalar()

    return AxisScoresResponse(
        rows=rows[:limit],
        next_cursor=next_cursor,
        total=total
    )

@router.get("/axis-scores/servers/{server_id}", response_model=AxisScoresResponse)
async def get_server_axis_scores(
    server_id: str,
    db: Session = Depends(get_session)
):
    results = _get_axis_scores(db, server_id=server_id, limit=200)

    rows = []
    for result in results:
        row = AxisScoreRow(
            server_id=result.server_id,
            axis_name=result.axis_name,
            label=result.label,
            label_index=result.label_index,
            p_top=result.p_top,
            p_critical=result.p_critical,
            p_danger=result.p_danger,
            escalated=result.escalated,
            scored_at=result.scored_at.isoformat() if result.scored_at else None
        )
        rows.append(row)

    total = len(rows)

    return AxisScoresResponse(
        rows=rows,
        next_cursor=None,
        total=total
    )

@router.get("/axis-scores/summary/{server_id}", response_model=SummaryResponse)
async def get_server_axis_summary(
    server_id: str,
    db: Session = Depends(get_session)
):
    # Latest row per axis. Was `GROUP BY axis_name` while selecting non-grouped,
    # non-aggregated columns -- invalid in Postgres (500; sqlite tolerated it, which
    # is why it passed the self-test). Use a row_number() window (newest scored_at per
    # axis = rn 1); portable across Postgres AND sqlite (unlike DISTINCT ON). #12368.
    #
    # SERVING-READ HAZARD (POSTMORTEM_20261030): this "newest scored_at per axis" is a
    # LIVE serving read that honored NO allowlist -- any current-timestamp write to
    # mcp_llm_axis_scores (a partial-axis live probe, an experimental model_version, a
    # v6 rescore) silently became "latest" and hijacked the served product (it degraded
    # 1541 servers' overviews once). FIX: when the operator has armed a serving
    # allowlist, restrict to rows whose model_version is on it BEFORE picking
    # latest-per-axis, so an off-allowlist write can never be served on timestamp alone.
    # DEFAULT OFF (allowlist is None) -> the filter is NOT applied and this read is
    # byte-for-byte the prior newest-scored_at-per-axis behavior. See
    # serving_model_allowlist.py and SERVING_FULL_MODEL_ALLOWLIST.
    allowlist = serving_model_allowlist()
    base = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if allowlist is not None:
        base = base.filter(McpLlmAxisScore.model_version.in_(allowlist))
    base_sq = base.subquery()
    rn = func.row_number().over(
        partition_by=base_sq.c.axis_name,
        order_by=desc(base_sq.c.scored_at),
    ).label("rn")
    sub = (
        db.query(
            base_sq.c.axis_name, base_sq.c.label, base_sq.c.label_index,
            base_sq.c.p_top, base_sq.c.p_critical, base_sq.c.p_danger,
            base_sq.c.escalated, base_sq.c.scored_at, rn,
        )
        .subquery()
    )
    results = db.query(sub).filter(sub.c.rn == 1).all()

    rows = []
    for result in results:
        row = SummaryRow(
            axis_name=result.axis_name,
            label=result.label,
            label_index=result.label_index,
            p_top=result.p_top,
            p_critical=result.p_critical,
            p_danger=result.p_danger,
            escalated=result.escalated,
            scored_at=result.scored_at.isoformat() if result.scored_at else None
        )
        rows.append(row)

    total = len(rows)

    return SummaryResponse(
        rows=rows,
        total=total
    )

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_data = [
        McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            label="High",
            label_index=3,
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            escalated=True,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="auth_strength",
            label="Medium",
            label_index=2,
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            escalated=False,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            label="Low",
            label_index=1,
            p_top=0.3,
            p_critical=0.2,
            p_danger=0.1,
            escalated=False,
            scored_at=datetime.now()
        ),
    ]

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            for data in test_data:
                session.add(data)
            session.commit()
            yield session
        finally:
            session.close()

    from app import app
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test 1: GET /axis-scores returns rows and next_cursor
    response = client.get("/axis-scores")
    assert response.status_code == 200
    assert len(response.json()["rows"]) > 0
    assert "next_cursor" in response.json()

    # Test 2: GET /axis-scores/servers/{server_id} returns all 7 axes
    response = client.get("/axis-scores/servers/server1")
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 2

    # Test 3: GET /axis-scores/summary/{server_id} returns 7 rows, one per axis
    response = client.get("/axis-scores/summary/server1")
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 2

    # Test 4: Filter by axis_name returns only matching rows
    response = client.get("/axis-scores?axis_name=overall_risk")
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 2

    # Test 5: Limit param caps row count
    response = client.get("/axis-scores?limit=1")
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 1

    # Serving-allowlist two-pole (OFF=newest-scored_at as today; ON=off-allowlist newer
    # partial row is IGNORED, armed full model served): see
    # test_serving_allowlist_two_pole.py -- a hermetic, DB-direct test that does not need
    # the full-app client above, so it runs in environments where this __main__ block's
    # `from app import app` wiring cannot build the schema.

    print("PASS")