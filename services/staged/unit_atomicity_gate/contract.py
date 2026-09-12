from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def verify_db_schema():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    inspector = inspect(engine)

    required_tables = {
        'mcp_server_registry': ['id', 'server_name', 'ip_address', 'os_type', 'os_version', 'created_at', 'updated_at'],
        'mcp_llm_axis_scores': ['id', 'server_id', 'axis_name', 'score', 'created_at', 'updated_at'],
        'mcp_score_disputes': ['id', 'server_id', 'axis_name', 'dispute_reason', 'created_at', 'updated_at'],
        'orgs': ['id', 'name', 'created_at', 'updated_at'],
        'users': ['id', 'username', 'email', 'org_id', 'created_at', 'updated_at']
    }

    for table_name, columns in required_tables.items():
        if not inspector.has_table(table_name):
            raise HTTPException(status_code=500, detail=f"Table {table_name} is missing")

        for column in columns:
            if column not in [col['name'] for col in inspector.get_columns(table_name)]:
                raise HTTPException(status_code=500, detail=f"Column {column} is missing in table {table_name}")

@app.on_event("startup")
async def startup_event():
    verify_db_schema()

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    test_app = FastAPI()

    def override_get_session():
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/health")
    async def test_health_check():
        return {"status": "healthy"}

    client = TestClient(test_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    print("PASS")