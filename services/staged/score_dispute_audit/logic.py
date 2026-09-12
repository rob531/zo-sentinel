from typing import Literal
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute


class DisputeAuditItem(BaseModel):
    server_id: int
    server_name: str
    proposed_overall_risk: float
    reason_category: str
    created_at: str
    status: str


class DisputeAuditResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    recent: list[DisputeAuditItem]


def get_audit_query(status_filter: Literal["open", "resolved", "all"] = "all") -> str:
    status_condition = ""
    if status_filter != "all":
        status_condition = f"WHERE d.status = '{status_filter}'"
    
    return f"""
        SELECT 
            d.server_id,
            s.name as server_name,
            d.proposed_overall_risk,
            d.reason_category,
            d.created_at,
            d.status
        FROM McpScoreDispute d
        JOIN McpServerRegistry s ON d.server_id = s.server_id
        {status_condition}
        ORDER BY d.created_at DESC
        LIMIT 50
    """


def get_by_status_query() -> str:
    return """
        SELECT status, COUNT(*) as count
        FROM McpScoreDispute
        GROUP BY status
    """


def get_total_count_query(status_filter: Literal["open", "resolved", "all"] = "all") -> tuple[str, dict]:
    if status_filter == "all":
        return "SELECT COUNT(*) as total FROM McpScoreDispute", {}
    return "SELECT COUNT(*) as total FROM McpScoreDispute WHERE status = :status", {"status": status_filter}


def get_dispute_audit(
    status: Literal["open", "resolved", "all"] = "all",
    session: Session = Depends(get_session)
) -> DisputeAuditResponse:
    total_query, total_params = get_total_count_query(status)
    total_result = session.execute(text(total_query), total_params).fetchone()
    total = total_result[0] if total_result else 0
    
    by_status_query = get_by_status_query()
    by_status_result = session.execute(text(by_status_query)).fetchall()
    by_status = {row[0]: row[1] for row in by_status_result}
    
    audit_query = get_audit_query(status)
    audit_result = session.execute(text(audit_query)).fetchall()
    
    recent = [
        DisputeAuditItem(
            server_id=row[0],
            server_name=row[1],
            proposed_overall_risk=row[2],
            reason_category=row[3],
            created_at=str(row[4]),
            status=row[5]
        )
        for row in audit_result
    ]
    
    return DisputeAuditResponse(total=total, by_status=by_status, recent=recent)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from main import app
    
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                confidence REAL DEFAULT 0.0
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpScoreDispute (
                dispute_id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                proposed_overall_risk REAL NOT NULL,
                reason_category TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (server_id) REFERENCES McpServerRegistry(server_id)
            )
        """))
        conn.commit()
        
        conn.execute(text("INSERT INTO McpServerRegistry (server_id, name) VALUES (1, 'TestServer-A')"))
        conn.execute(text("INSERT INTO McpServerRegistry (server_id, name) VALUES (2, 'TestServer-B')"))
        conn.execute(text("INSERT INTO McpServerRegistry (server_id, name) VALUES (3, 'TestServer-C')"))
        conn.execute(text("INSERT INTO McpScoreDispute (server_id, proposed_overall_risk, reason_category, created_at, status) VALUES (1, 7.5, 'overstated_risk', '2024-01-15', 'open')"))
        conn.execute(text("INSERT INTO McpScoreDispute (server_id, proposed_overall_risk, reason_category, created_at, status) VALUES (2, 3.2, 'understated_risk', '2024-01-16', 'open')"))
        conn.execute(text("INSERT INTO McpScoreDispute (server_id, proposed_overall_risk, reason_category, created_at, status) VALUES (3, 5.0, 'wrong_category', '2024-01-17', 'resolved')"))
        conn.commit()
    
    def override_get_session():
        return SessionLocal()
    
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    response = client.get("/api/disputes/audit?status=all")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert data["total"] == 3, f"Expected total=3, got {data['total']}"
    assert data["by_status"]["open"] == 2, f"Expected by_status open=2, got {data['by_status']}"
    
    print("PASS")