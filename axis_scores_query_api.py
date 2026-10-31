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
    rn = func.row_number().over(
        partition_by=McpLlmAxisScore.axis_name,
        order_by=desc(McpLlmAxisScore.scored_at),
    ).label("rn")
    sub = (
        db.query(
            McpLlmAxisScore.axis_name, McpLlmAxisScore.label, McpLlmAxisScore.label_index,
            McpLlmAxisScore.p_top, McpLlmAxisScore.p_critical, McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated, McpLlmAxisScore.scored_at, rn,
        )
        .filter(McpLlmAxisScore.server_id == server_id)
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

    print("PASS")