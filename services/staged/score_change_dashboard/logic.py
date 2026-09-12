from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/scores", tags=["scores"])


class AxisScoreChange(BaseModel):
    old_score: float | None
    new_score: float | None
    change: float | None


@router.get("/change")
def get_score_changes(session: Session = Depends(get_session)) -> dict[str, dict[str, AxisScoreChange]]:
    query = text("""
        WITH ranked AS (
            SELECT
                server_id,
                scored_at,
                axis_name,
                p_critical,
                p_danger,
                p_top,
                ROW_NUMBER() OVER (PARTITION BY server_id, axis_name ORDER BY scored_at DESC) as rn
            FROM McpLlmAxisScore
        ),
        with_prev AS (
            SELECT
                server_id,
                axis_name,
                p_critical,
                p_danger,
                p_top,
                LAG(p_critical) OVER (PARTITION BY server_id, axis_name ORDER BY scored_at) as prev_critical,
                LAG(p_danger) OVER (PARTITION BY server_id, axis_name ORDER BY scored_at) as prev_danger,
                LAG(p_top) OVER (PARTITION BY server_id, axis_name ORDER BY scored_at) as prev_top
            FROM McpLlmAxisScore
        )
        SELECT
            server_id,
            axis_name,
            p_critical as new_critical,
            p_danger as new_danger,
            p_top as new_top,
            prev_critical,
            prev_danger,
            prev_top
        FROM with_prev
        WHERE (prev_critical IS NOT NULL AND prev_critical != p_critical)
           OR (prev_danger IS NOT NULL AND prev_danger != p_danger)
           OR (prev_top IS NOT NULL AND prev_top != p_top)
    """)
    
    result: dict[str, dict[str, AxisScoreChange]] = {}
    rows = session.execute(query).fetchall()
    
    for row in rows:
        server_id = row.server_id
        axis_name = row.axis_name
        
        if axis_name == "critical":
            old_score = row.prev_critical
            new_score = row.new_critical
        elif axis_name == "danger":
            old_score = row.prev_danger
            new_score = row.new_danger
        else:
            old_score = row.prev_top
            new_score = row.new_top
        
        if server_id not in result:
            result[server_id] = {}
        
        change = (new_score - old_score) if (old_score is not None and new_score is not None) else None
        result[server_id][axis_name] = AxisScoreChange(
            old_score=old_score,
            new_score=new_score,
            change=change
        )
    
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(255) NOT NULL,
                axis_name VARCHAR(50) NOT NULL,
                p_critical FLOAT,
                p_danger FLOAT,
                p_top FLOAT,
                probs TEXT,
                label VARCHAR(50),
                label_index INTEGER,
                adapter_sha256 VARCHAR(64),
                model_version VARCHAR(50),
                decision_rule_version VARCHAR(50),
                scored_at TIMESTAMP NOT NULL,
                escalated BOOLEAN DEFAULT 0,
                escalated_to VARCHAR(50)
            )
        """))
        conn.commit()
        
        records = [
            ("server_1", "critical", 0.8, None, None, "2024-01-01 10:00:00"),
            ("server_1", "critical", 0.9, None, None, "2024-01-01 12:00:00"),
            ("server_1", "danger", 0.5, None, None, "2024-01-01 10:00:00"),
            ("server_1", "danger", 0.3, None, None, "2024-01-01 12:00:00"),
            ("server_1", "top", 0.2, None, None, "2024-01-01 10:00:00"),
            ("server_1", "top", 0.4, None, None, "2024-01-01 12:00:00"),
            ("server_2", "critical", 0.7, None, None, "2024-01-01 10:00:00"),
            ("server_2", "critical", 0.6, None, None, "2024-01-01 12:00:00"),
            ("server_2", "danger", 0.4, None, None, "2024-01-01 10:00:00"),
            ("server_2", "danger", 0.6, None, None, "2024-01-01 12:00:00"),
            ("server_2", "top", 0.3, None, None, "2024-01-01 10:00:00"),
            ("server_2", "top", 0.5, None, None, "2024-01-01 12:00:00"),
            ("server_3", "critical", 0.6, None, None, "2024-01-01 10:00:00"),
            ("server_3", "critical", 0.85, None, None, "2024-01-01 12:00:00"),
            ("server_3", "danger", 0.3, None, None, "2024-01-01 10:00:00"),
            ("server_3", "danger", 0.7, None, None, "2024-01-01 12:00:00"),
            ("server_3", "top", 0.1, None, None, "2024-01-01 10:00:00"),
            ("server_3", "top", 0.35, None, None, "2024-01-01 12:00:00"),
        ]
        
        for server_id, axis_name, p_critical, p_danger, p_top, scored_at in records:
            conn.execute(text("""
                INSERT INTO McpLlmAxisScore 
                (server_id, axis_name, p_critical, p_danger, p_top, scored_at, probs, label, label_index, adapter_sha256, model_version, decision_rule_version)
                VALUES (:server_id, :axis_name, :p_critical, :p_danger, :p_top, :scored_at, '[]', 'test', 0, 'sha256test', 'v1', 'v1')
            """), {"server_id": server_id, "axis_name": axis_name, "p_critical": p_critical, "p_danger": p_danger, "p_top": p_top, "scored_at": scored_at})
        conn.commit()
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/api/scores/change")
    
    assert response.status_code == 200
    data = response.json()
    servers_with_changes = list(data.keys())
    assert len(servers_with_changes) >= 3, f"Expected at least 3 servers with changes, got {len(servers_with_changes)}: {servers_with_changes}"
    
    for server_id in servers_with_changes:
        assert server_id in ["server_1", "server_2", "server_3"]
        for axis, scores in data[server_id].items():
            assert "old_score" in scores
            assert "new_score" in scores
            assert "change" in scores
    
    print("PASS")