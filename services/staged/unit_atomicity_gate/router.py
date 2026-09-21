from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

@router.get("/health")
async def health_check(session: Session = Depends(get_session)):
    required_tables = {
        "mcp_server_registry": ["id", "name", "org_id"],
        "mcp_llm_axis_scores": ["id", "server_id", "axis_name", "score", "risk_tier"],
        "mcp_score_disputes": ["id", "server_id", "axis_name", "dispute_reason"],
        "orgs": ["id", "name"],
        "users": ["id", "username", "org_id"]
    }

    for table, columns in required_tables.items():
        model = globals()[table.capitalize()]
        for column in columns:
            if not hasattr(model, column):
                return {"status": "error", "message": f"missing column {column} in table {table}"}

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    uvicorn.run(app, host="127.0.0.1", port=8000)