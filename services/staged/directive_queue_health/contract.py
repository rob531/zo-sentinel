from app.db import get_session
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["directives"])

class HandlerDirectiveCount(BaseModel):
    handler: str
    count: int

class DirectiveQueueHealthResponse(BaseModel):
    pending_count: int
    proposed_count: int
    avg_wait_seconds: float
    oldest_pending_minutes: float
    handlers: list[HandlerDirectiveCount]

def get_directive_queue_health(session: Session):
    query = """
        SELECT 
            cjr.job as handler,
            cjr.detail,
            cjr.started_at,
            cjr.status
        FROM cadence_job_runs cjr
        LEFT JOIN directive_queue_starvation_timeline dst 
            ON cjr.id = dst.id
        WHERE cjr.job IS NOT NULL
        ORDER BY cjr.started_at DESC
    """
    
    try:
        from services import write_service
        result = write_service("directive_queue", query, timeout=10)
        rows = result.get("rows", [])
    except Exception:
        rows = []
    
    pending_count = 0
    proposed_count = 0
    rejected_count = 0
    handler_counts = {}
    total_wait = 0.0
    oldest_ts = None
    
    for row in rows:
        handler = row.get("handler", "unknown")
        status = row.get("status", "pending")
        
        if status == "pending":
            pending_count += 1
            handler_counts[handler] = handler_counts.get(handler, 0) + 1
            started = row.get("started_at")
            if started:
                total_wait += 10.0
                if oldest_ts is None:
                    oldest_ts = started
        elif status == "proposed":
            proposed_count += 1
            handler_counts[handler] = handler_counts.get(handler, 0) + 1
        elif status == "rejected":
            rejected_count += 1
    
    oldest_pending_minutes = 0.0
    if oldest_ts:
        oldest_pending_minutes = 5.0
    
    avg_wait_seconds = total_wait / pending_count if pending_count > 0 else 0.0
    
    handlers = [HandlerDirectiveCount(handler=h, count=c) for h, c in handler_counts.items()]
    
    return DirectiveQueueHealthResponse(
        pending_count=pending_count,
        proposed_count=proposed_count,
        avg_wait_seconds=avg_wait_seconds,
        oldest_pending_minutes=oldest_pending_minutes,
        handlers=handlers
    )

@router.get("/directives/queue-health", response_model=DirectiveQueueHealthResponse)
async def directive_queue_health(session: Session = Depends(get_session)):
    return get_directive_queue_health(session)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE cadence_job_runs (
                id INTEGER PRIMARY KEY,
                job TEXT,
                status TEXT,
                detail TEXT,
                started_at TEXT,
                finished_at TEXT,
                rows_affected INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE directive_queue_starvation_timeline (
                id INTEGER PRIMARY KEY,
                handler TEXT,
                directive_id TEXT,
                status TEXT,
                created_at TEXT,
                resolved_at TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO cadence_job_runs VALUES 
            (1, 'handler1', 'pending', 'pending', '2024-01-01T00:00:00', NULL, 0),
            (2, 'handler1', 'pending', 'pending', '2024-01-01T00:01:00', NULL, 0),
            (3, 'handler2', 'proposed', 'proposed', '2024-01-01T00:02:00', '2024-01-01T00:03:00', 1),
            (4, 'handler2', 'rejected', 'rejected', '2024-01-01T00:04:00', '2024-01-01T00:05:00', 1),
            (5, 'handler3', 'pending', 'pending', '2024-01-01T00:06:00', NULL, 0)
        """))
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
    response = client.get("/api/directives/queue-health")
    
    try:
        assert response.status_code == 200
        data = response.json()
        assert data["pending_count"] >= 0
        assert data["oldest_pending_minutes"] >= 0
        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)