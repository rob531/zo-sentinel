"""run_reconciliation_report router."""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["scorer"])


class ReconciliationRow(BaseModel):
    server_id: str
    fire_score: float
    final_score: float
    difference: float


class ReconciliationReport(BaseModel):
    rows: list[ReconciliationRow]


@router.get("/scorer/reconciliation", response_model=ReconciliationReport)
def get_reconciliation_report(
    session: Annotated[Session, Depends(get_session)],
) -> ReconciliationReport:
    """Compute reconciliation report: fire_score vs final_score differences."""
    result = session.execute(
        text("""
            SELECT
                r.server_id,
                r.fire_score,
                r.final_score,
                (r.final_score - r.fire_score) AS difference
            FROM mcp_score_run_ledger r
            JOIN mcp_server_registry s ON s.server_id = r.server_id
            ORDER BY r.server_id
        """)
    )
    rows = [
        ReconciliationRow(
            server_id=row.server_id,
            fire_score=row.fire_score,
            final_score=row.final_score,
            difference=row.difference,
        )
        for row in result.fetchall()
    ]
    return ReconciliationReport(rows=rows)


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session as SASession
    from sqlalchemy.pool import StaticPool

    # in-memory self-test with seeded data
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                confidence REAL,
                trust_score REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE mcp_score_run_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                fire_score REAL,
                final_score REAL,
                scored_at TEXT
            )
        """)

        # seed 2 servers with 7 axes each
        servers = [
            ("srv-001", "Server One"),
            ("srv-002", "Server Two"),
        ]
        for sid, name in servers:
            conn.exec_driver_sql(
                "INSERT INTO mcp_server_registry (server_id, name) VALUES (:s, :n)",
                {"s": sid, "n": name},
            )

        axes = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
        for sid, name in servers:
            for axis in axes:
                fire = axis
                final = axis + 0.5 if sid == "srv-001" else axis
                conn.exec_driver_sql(
                    "INSERT INTO mcp_score_run_ledger (server_id, fire_score, final_score) VALUES (:s, :f, :ff)",
                    {"s": sid, "f": fire, "ff": final},
                )

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session() -> SASession:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient

    that_app = app
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/scorer/reconciliation")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    rows = data.get("rows", [])
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    differences = [r["difference"] for r in rows]
    assert any(d > 0 for d in differences), f"Expected at least one positive difference, got {differences}"

    print("PASS")