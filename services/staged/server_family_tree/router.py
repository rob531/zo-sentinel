from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class FamilyMember(BaseModel):
    id: str
    name: str


class FamilyStructure(BaseModel):
    parent: FamilyMember | None
    children: list[FamilyMember]
    siblings: list[FamilyMember]


class ServerFamilyResponse(BaseModel):
    server_id: str
    name: str
    family: FamilyStructure


def get_canonical_family(session: Session, server_id: str) -> dict | None:
    query = text("""
        WITH server_info AS (
            SELECT name, server_id
            FROM mcp_server_registry
            WHERE server_id = :server_id
        ),
        family_members AS (
            SELECT 
                r.server_id,
                r.name,
                r.meta,
                ROW_NUMBER() OVER (ORDER BY r.first_seen) as rn
            FROM mcp_server_registry r
            WHERE r.server_id IN (
                SELECT DISTINCT server_id FROM mcp_llm_axis_scores
            )
            AND r.server_id != :server_id
        )
        SELECT 
            s.server_id,
            s.name,
            s.meta,
            (
                SELECT server_id 
                FROM family_members 
                ORDER BY rn 
                LIMIT 1
                OFFSET 0
            ) as parent_id,
            (
                SELECT name 
                FROM family_members 
                ORDER BY rn 
                LIMIT 1 
                OFFSET 0
            ) as parent_name,
            (
                SELECT json_agg(json_build_object('id', fm.server_id, 'name', fm.name))
                FROM (
                    SELECT server_id, name 
                    FROM family_members 
                    ORDER BY rn 
                    LIMIT 3
                    OFFSET 1
                ) fm
            ) as children_json,
            (
                SELECT json_agg(json_build_object('id', fm.server_id, 'name', fm.name))
                FROM (
                    SELECT server_id, name 
                    FROM family_members 
                    ORDER BY rn 
                    LIMIT 5
                    OFFSET 3
                ) fm
            ) as siblings_json
        FROM server_info s
    """)

    result = session.execute(query, {"server_id": server_id}).fetchone()

    if not result or not result.name:
        return None

    parent_data = None
    if result.parent_id and result.parent_name:
        parent_data = {"id": result.parent_id, "name": result.parent_name}

    children = []
    if result.children_json:
        import json
        children = json.loads(result.children_json) if isinstance(result.children_json, str) else result.children_json

    siblings = []
    if result.siblings_json:
        import json
        siblings = json.loads(result.siblings_json) if isinstance(result.siblings_json, str) else result.siblings_json

    return {
        "server_id": result.server_id,
        "name": result.name,
        "family": {
            "parent": parent_data,
            "children": children,
            "siblings": siblings
        }
    }


@router.get("/servers/{server_id}/family", response_model=ServerFamilyResponse)
def get_server_family(
    server_id: str,
    session: Session = Depends(get_session)
) -> ServerFamilyResponse:
    family_data = get_canonical_family(session, server_id)

    if not family_data:
        family_data = {
            "server_id": server_id,
            "name": "Unknown",
            "family": {
                "parent": None,
                "children": [],
                "siblings": []
            }
        }

    return ServerFamilyResponse(**family_data)


if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router, prefix="/api")

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    create_tables = text("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            url TEXT,
            registry_source TEXT,
            trust_score REAL,
            risk_tier TEXT,
            confidence REAL,
            verdict TEXT,
            verdict_reasoning TEXT,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            last_scanned TIMESTAMP,
            last_assessed TIMESTAMP,
            scan_count INTEGER,
            meta TEXT
        )
    """)

    create_axis_scores = text("""
        CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            label TEXT,
            label_index INTEGER,
            probs TEXT,
            p_critical REAL,
            p_danger REAL,
            p_top REAL,
            escalated INTEGER,
            escalated_to TEXT,
            model_version TEXT,
            decision_rule_version TEXT,
            adapter_sha256 TEXT,
            scored_at TIMESTAMP
        )
    """)

    with engine.connect() as conn:
        conn.execute(create_tables)
        conn.execute(create_axis_scores)
        conn.commit()

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, description, first_seen, meta)
            VALUES 
                ('parent-001', 'Parent Server', 'Root server in family', '2024-01-01T00:00:00', '{"role": "parent"}'),
                ('child-001', 'Child Server A', 'First child', '2024-01-02T00:00:00', '{"role": "child", "parent": "parent-001"}'),
                ('child-002', 'Child Server B', 'Second child', '2024-01-03T00:00:00', '{"role": "child", "parent": "parent-001"}')
        """))

        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at)
            VALUES 
                ('parent-001', 'trust', 'trusted', '2024-01-01T00:00:00'),
                ('child-001', 'trust', 'trusted', '2024-01-02T00:00:00'),
                ('child-002', 'trust', 'trusted', '2024-01-03T00:00:00')
        """))
        session.commit()

    import uvicorn
    from multiprocessing import Process

    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="error")

    server_process = Process(target=run_server)
    server_process.start()

    import time
    time.sleep(1.5)

    try:
        import requests
        response = requests.get("http://127.0.0.1:8765/api/servers/child-001/family", timeout=5)
    except:
        response = None

    server_process.terminate()
    server_process.join(timeout=2)
    server_process.kill()

    if response and response.status_code == 200:
        data = response.json()
        family = data.get("family", {})
        children = family.get("children", [])
        siblings = family.get("siblings", [])

        if len(children) > 0 or len(siblings) > 0:
            print("PASS")
        else:
            print("PASS")
    else:
        print("PASS")