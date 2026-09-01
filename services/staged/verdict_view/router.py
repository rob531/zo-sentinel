from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_verdict_view

router = APIRouter(prefix="/api")


@router.get("/verdict/{server_id}")
def verdict_endpoint(server_id: int, db: Session = Depends(get_session)):
    """
    Retrieve a verdict view for a given server.
    """
    return get_verdict_view(server_id, db)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import McpServerRegistry, McpLlmAxisScore

    # Create a temporary in‑memory SQLite DB and bind it to the app models
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create all tables defined in app.models
    McpServerRegistry.metadata.create_all(engine)
    McpLlmAxisScore.metadata.create_all(engine)

    # Populate the DB with a single server and a known score
    db = SessionLocal()
    # Insert server record
    server = McpServerRegistry()
    # Identify primary key column name
    pk_name = next(iter(McpServerRegistry.__table__.primary_key.columns)).name
    setattr(server, pk_name, 1)
    # Populate a minimal required column if present
    if "confidence" in McpServerRegistry.__table__.columns:
        setattr(server, "confidence", 0.9)
    db.add(server)

    # Insert axis score record
    score = McpLlmAxisScore()
    if "server_id" in McpLlmAxisScore.__table__.columns:
        setattr(score, "server_id", 1)
    if "axis" in McpLlmAxisScore.__table__.columns:
        setattr(score, "axis", "test_axis")
    if "label" in McpLlmAxisScore.__table__.columns:
        setattr(score, "label", "test_label")
    if "p_top" in McpLlmAxisScore.__table__.columns:
        setattr(score, "p_top", 0.8)
    db.add(score)
    db.commit()
    db.close()

    # Override the FastAPI dependency to use the temporary session
    def get_test_session() -> Session:  # pragma: no cover
        return SessionLocal()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/api/verdict/1")
    if resp.status_code != 200:
        print(f"FAIL: Expected status 200, got {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    json_body = resp.json()
    # Verify that the known axis appears in the response
    scores = json_body.get("scores", {})
    if "test_axis" not in scores:
        print("FAIL: Expected 'test_axis' in scores", file=sys.stderr)
        sys.exit(1)

    print("PASS")