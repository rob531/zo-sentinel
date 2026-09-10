from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session

router = APIRouter()


class ReconciliationReportItem(BaseModel):
    server_id: str
    fire_score: float
    final_score: float
    difference: float

    class Config:
        from_attributes = True


class ReconciliationReportResponse(BaseModel):
    data: List[ReconciliationReportItem]


def compute_reconciliation_report(
    session: Session,
    axis: Optional[str] = None,
    server_id: Optional[str] = None,
) -> List[ReconciliationReportItem]:
    axis_filter = f"AND axis = :axis" if axis else ""
    server_filter = f"AND server_id = :server_id" if server_id else ""

    query = text(f"""
        SELECT
            a.server_id,
            AVG(a.score) as fire_score,
            MAX(a.score) as final_score
        FROM mcp_llm_axis_scores a
        WHERE 1=1 {axis_filter} {server_filter}
        GROUP BY a.server_id
    """)

    params = {}
    if axis:
        params["axis"] = axis
    if server_id:
        params["server_id"] = server_id

    result = session.execute(query, params)
    rows = result.fetchall()

    return [
        ReconciliationReportItem(
            server_id=row.server_id,
            fire_score=round(row.fire_score, 4),
            final_score=round(row.final_score, 4),
            difference=round(row.final_score - row.fire_score, 4)
        )
        for row in rows
    ]


@router.get("/api/scorer/reconciliation", response_model=ReconciliationReportResponse)
async def get_reconciliation_report(
    axis: Optional[str] = Query(None),
    server_id: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    report = compute_reconciliation_report(session, axis=axis, server_id=server_id)
    return ReconciliationReportResponse(data=report)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (server_id TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE mcp_llm_axis_scores (id INTEGER PRIMARY KEY, server_id TEXT, axis TEXT, score REAL)"))
        conn.commit()

        for i, (sid, name) in enumerate([("srv-001", "Alpha"), ("srv-002", "Beta")]):
            conn.execute(text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"), {"sid": sid, "name": name})
            for ax in range(7):
                base = 0.6 + (i * 0.1)
                conn.execute(
                    text("INSERT INTO mcp_llm_axis_scores (server_id, axis, score) VALUES (:sid, :axis, :score)"),
                    {"sid": sid, "axis": f"AX-{ax}", "score": base + (ax * 0.05)}
                )
        conn.commit()

    def override_get_session():
        with engine.connect() as conn:
            yield conn

    that_app = FastAPI()
    that_app.include_router(router)

    from fastapi.testclient import TestClient
    that_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(that_app)

    resp = client.get("/api/scorer/reconciliation")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    assert data.get("data"), "Expected non-empty data"
    assert any(item["difference"] > 0 for item in data["data"]), "Expected at least one server with difference > 0"

    engine.dispose()
    print("PASS")