from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


class DisputeRecord(BaseModel):
    id: int
    server_id: int
    server_name: str
    status: str
    reason_category: str
    created_at: datetime
    submitted_by: str


class SummaryResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_reason: dict[str, int]


class BacklogResponse(BaseModel):
    summary: SummaryResponse
    disputes: list[DisputeRecord]


def get_disputes_backlog(db: Session = Depends(get_session)) -> dict[str, Any]:
    cutoff_date = datetime.utcnow() - timedelta(days=90)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")

    status_query = text("""
        SELECT status, COUNT(*) as cnt
        FROM mcp_score_disputes
        WHERE created_at >= :cutoff
        GROUP BY status
    """)

    reason_query = text("""
        SELECT reason_category, COUNT(*) as cnt
        FROM mcp_score_disputes
        WHERE created_at >= :cutoff
        GROUP BY reason_category
    """)

    disputes_query = text("""
        SELECT
            d.id,
            d.server_id,
            s.name as server_name,
            d.status,
            d.reason_category,
            d.created_at,
            d.submitted_by
        FROM mcp_score_disputes d
        JOIN mcp_server_registry s ON d.server_id = s.server_id
        WHERE d.created_at >= :cutoff
        ORDER BY d.created_at DESC
    """)

    status_result = db.execute(status_query, {"cutoff": cutoff_str}).fetchall()
    reason_result = db.execute(reason_query, {"cutoff": cutoff_str}).fetchall()
    disputes_result = db.execute(disputes_query, {"cutoff": cutoff_str}).fetchall()

    by_status = {row[0]: row[1] for row in status_result if row[0] is not None}
    by_reason = {row[0]: row[1] for row in reason_result if row[0] is not None}
    total = sum(by_status.values())

    disputes = [
        DisputeRecord(
            id=row[0],
            server_id=row[1],
            server_name=row[2],
            status=row[3],
            reason_category=row[4],
            created_at=row[5],
            submitted_by=row[6],
        )
        for row in disputes_result
    ]

    return {
        "summary": {"total": total, "by_status": by_status, "by_reason": by_reason},
        "disputes": disputes,
    }


@router.get("/api/disputes/backlog", response_model=BacklogResponse)
def endpoint(db: Session = Depends(get_session)) -> dict[str, Any]:
    return get_disputes_backlog(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    create_sql = """
        CREATE TABLE mcp_server_registry (
            server_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT,
            description TEXT,
            registry_source TEXT,
            risk_tier TEXT,
            verdict TEXT,
            verdict_reasoning TEXT,
            trust_score REAL,
            confidence REAL,
            meta TEXT,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            last_scanned TIMESTAMP,
            last_assessed TIMESTAMP,
            scan_count INTEGER
        );

        CREATE TABLE mcp_score_disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            status TEXT,
            reason_category TEXT,
            submitted_by TEXT,
            explanation TEXT,
            proposed_axes TEXT,
            proposed_overall_risk TEXT,
            admin_note TEXT,
            created_at TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
        );
    """

    with engine.begin() as conn:
        for stmt in create_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))

    now = datetime.utcnow()
    seed_data = [
        ("server1", "open", "accuracy", "user_a"),
        ("server2", "resolved", "threshold", "user_b"),
        ("server3", "pending_review", "methodology", "user_c"),
        ("server1", "open", "accuracy", "user_d"),
        ("server2", "pending_review", "threshold", "user_e"),
    ]

    insert_server = text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:id, :name)")
    insert_dispute = text(
        "INSERT INTO mcp_score_disputes (server_id, status, reason_category, submitted_by, created_at) "
        "VALUES (:server_id, :status, :reason_category, :submitted_by, :created_at)"
    )

    with engine.begin() as conn:
        for idx, (name,) in enumerate([("server1",), ("server2",), ("server3",)], start=1):
            conn.execute(insert_server, {"id": idx, "name": name})

        for idx, (server_name, status, reason, user) in enumerate(seed_data, start=1):
            server_id_map = {"server1": 1, "server2": 2, "server3": 3}
            conn.execute(
                insert_dispute,
                {
                    "server_id": server_id_map[server_name],
                    "status": status,
                    "reason_category": reason,
                    "submitted_by": user,
                    "created_at": now - timedelta(days=idx),
                },
            )

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    from fastapi.testclient import TestClient

    that_app = test_app
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/disputes/backlog")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    summary = data["summary"]

    assert summary["total"] == 5, f"Expected total=5, got {summary['total']}"

    status_counts = summary["by_status"]
    assert status_counts.get("open", 0) == 2, f"Expected open=2, got {status_counts.get('open')}"
    assert status_counts.get("resolved", 0) == 1, f"Expected resolved=1, got {status_counts.get('resolved')}"
    assert status_counts.get("pending_review", 0) == 2, f"Expected pending_review=2, got {status_counts.get('pending_review')}"

    reason_counts = summary["by_reason"]
    assert reason_counts.get("accuracy", 0) == 2, f"Expected accuracy=2, got {reason_counts.get('accuracy')}"
    assert reason_counts.get("threshold", 0) == 2, f"Expected threshold=2, got {reason_counts.get('threshold')}"
    assert reason_counts.get("methodology", 0) == 1, f"Expected methodology=1, got {reason_counts.get('methodology')}"

    assert len(data["disputes"]) == 5, f"Expected 5 disputes, got {len(data['disputes'])}"

    print("PASS")