from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session

router = APIRouter(prefix="/api", tags=["health"])


class DirectiveQueueHealthResponse(BaseModel):
    queue_depth: int
    processing_time: float
    failure_rate: float


def get_directive_queue_metrics(session: Session) -> DirectiveQueueHealthResponse:
    result = session.execute(
        text("""
            SELECT 
                COALESCE(queue_depth, 0) as queue_depth,
                COALESCE(processing_time, 0.0) as processing_time,
                COALESCE(failure_rate, 0.0) as failure_rate
            FROM directive_queue_health
            ORDER BY id DESC
            LIMIT 1
        """)
    )
    row = result.fetchone()
    if row:
        return DirectiveQueueHealthResponse(
            queue_depth=row.queue_depth,
            processing_time=float(row.processing_time),
            failure_rate=float(row.failure_rate)
        )
    return DirectiveQueueHealthResponse(queue_depth=0, processing_time=0.0, failure_rate=0.0)


@router.get("/health/directive_queue", response_model=DirectiveQueueHealthResponse)
def get_directive_queue_health(session: Session = Depends(get_session)):
    return get_directive_queue_metrics(session)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE directive_queue_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_depth INTEGER NOT NULL,
                processing_time REAL NOT NULL,
                failure_rate REAL NOT NULL
            )
        """))
        conn.execute(text("""
            INSERT INTO directive_queue_health (queue_depth, processing_time, failure_rate)
            VALUES 
                (42, 1.23, 0.05),
                (38, 0.87, 0.03),
                (55, 2.15, 0.08)
        """))
        conn.commit()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/health/directive_queue")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert data["queue_depth"] == 55, f"Expected queue_depth 55, got {data['queue_depth']}"
    assert data["processing_time"] == 2.15, f"Expected processing_time 2.15, got {data['processing_time']}"
    assert data["failure_rate"] == 0.08, f"Expected failure_rate 0.08, got {data['failure_rate']}"

    print("PASS")