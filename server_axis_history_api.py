from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from enum import Enum
import httpx
import time
from app.db import get_session
from app.models import MCPLLMAxisScore

router = APIRouter()

class AxisName(str, Enum):
    RISK = "risk"
    THREAT = "threat"
    VULNERABILITY = "vulnerability"
    IMPACT = "impact"
    LIKELIHOOD = "likelihood"
    EXPOSURE = "exposure"
    MITIGATION = "mitigation"

class AxisHistoryItem(BaseModel):
    scored_at: datetime
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    model_version: str
    adapter_sha256: str
    decision_rule_version: str

class AxisHistoryResponse(BaseModel):
    server_id: int
    total_rows: int
    filters_echoed: dict
    history: List[AxisHistoryItem]

def query_remote_db(sql: str, params: list = None) -> list:
    url = "http://127.0.0.1:8772/query"
    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            response = httpx.post(
                url,
                json={"sql": sql, "params": params},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            if attempt == max_retries - 1:
                raise HTTPException(status_code=503, detail="Service unavailable")
            time.sleep(retry_delay)
            retry_delay *= 2

async def get_axis_history(
    server_id: int,
    axis_name: Optional[AxisName] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 500,
    session: MCPLLMAxisScore = Depends(get_session)
) -> AxisHistoryResponse:
    if limit > 2000:
        limit = 2000

    query = """
        SELECT
            server_id,
            axis_name,
            label,
            label_index,
            probs,
            p_top,
            p_critical,
            p_danger,
            escalated,
            decision_rule_version,
            model_version,
            adapter_sha256,
            scored_at
        FROM mcp_llm_axis_scores
        WHERE server_id = ?
    """
    params = [server_id]

    if axis_name:
        query += " AND axis_name = ?"
        params.append(axis_name)

    if since:
        query += " AND scored_at >= ?"
        params.append(since)

    if until:
        query += " AND scored_at <= ?"
        params.append(until)

    query += " ORDER BY scored_at ASC LIMIT ?"
    params.append(limit)

    try:
        result = query_remote_db(query, params)
    except HTTPException as e:
        raise e

    history = []
    for row in result:
        history.append(AxisHistoryItem(
            scored_at=row["scored_at"],
            axis_name=row["axis_name"],
            label=row["label"],
            label_index=row["label_index"],
            p_top=row["p_top"],
            p_critical=row["p_critical"],
            p_danger=row["p_danger"],
            model_version=row["model_version"],
            adapter_sha256=row["adapter_sha256"],
            decision_rule_version=row["decision_rule_version"]
        ))

    total_rows = len(history)

    filters_echoed = {
        "axis_name": axis_name,
        "since": since,
        "until": until,
        "limit": limit
    }

    return AxisHistoryResponse(
        server_id=server_id,
        total_rows=total_rows,
        filters_echoed=filters_echoed,
        history=history
    )

@router.get("/servers/{server_id}/axis-history", response_model=AxisHistoryResponse)
async def axis_history(
    server_id: int,
    axis_name: Optional[AxisName] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    limit: int = Query(500, le=2000),
    session: MCPLLMAxisScore = Depends(get_session)
):
    return await get_axis_history(server_id, axis_name, since, until, limit, session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Override the get_session dependency for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        test_data = [
            {
                "server_id": 1,
                "axis_name": "risk",
                "label": "high",
                "label_index": 3,
                "probs": [0.1, 0.2, 0.3, 0.4],
                "p_top": 0.4,
                "p_critical": 0.3,
                "p_danger": 0.2,
                "escalated": False,
                "decision_rule_version": "1.0",
                "model_version": "v1",
                "adapter_sha256": "a1b2c3",
                "scored_at": datetime(2023, 1, 1, 12, 0)
            },
            {
                "server_id": 1,
                "axis_name": "threat",
                "label": "medium",
                "label_index": 2,
                "probs": [0.1, 0.2, 0.3, 0.4],
                "p_top": 0.3,
                "p_critical": 0.2,
                "p_danger": 0.1,
                "escalated": False,
                "decision_rule_version": "1.0",
                "model_version": "v1",
                "adapter_sha256": "a1b2c3",
                "scored_at": datetime(2023, 1, 2, 12, 0)
            },
            {
                "server_id": 2,
                "axis_name": "risk",
                "label": "low",
                "label_index": 1,
                "probs": [0.1, 0.2, 0.3, 0.4],
                "p_top": 0.2,
                "p_critical": 0.1,
                "p_danger": 0.0,
                "escalated": False,
                "decision_rule_version": "1.0",
                "model_version": "v1",
                "adapter_sha256": "a1b2c3",
                "scored_at": datetime(2023, 1, 3, 12, 0)
            },
            {
                "server_id": 2,
                "axis_name": "risk",
                "label": "high",
                "label_index": 3,
                "probs": [0.1, 0.2, 0.3, 0.4],
                "p_top": 0.4,
                "p_critical": 0.3,
                "p_danger": 0.2,
                "escalated": False,
                "decision_rule_version": "1.0",
                "model_version": "v1",
                "adapter_sha256": "a1b2c3",
                "scored_at": datetime(2023, 1, 4, 12, 0)
            }
        ]

        for data in test_data:
            session.add(MCPLLMAxisScore(**data))
        session.commit()

    client = TestClient(app)

    # Test 1: Check history is sorted by scored_at ASC
    response = client.get("/servers/1/axis-history")
    assert response.status_code == 200
    history = response.json()["history"]
    assert len(history) == 2
    assert history[0]["scored_at"] < history[1]["scored_at"]

    # Test 2: Check axis_name filter narrows to 2 rows
    response = client.get("/servers/1/axis-history?axis_name=risk")
    assert response.status_code == 200
    history = response.json()["history"]
    assert len(history) == 1

    # Test 3: Check since=<midpoint> returns 1 row
    midpoint = datetime(2023, 1, 1, 18, 0).isoformat()
    response = client.get(f"/servers/1/axis-history?since={midpoint}")
    assert response.status_code == 200
    history = response.json()["history"]
    assert len(history) == 1

    # Test 4: Check unknown server_id returns empty list with total_rows==0
    response = client.get("/servers/999/axis-history")
    assert response.status_code == 200
    assert response.json()["total_rows"] == 0
    assert len(response.json()["history"]) == 0

    print("PASS")