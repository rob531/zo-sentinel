from datetime import datetime
from typing import List, Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

# Application DB imports (must be used for production code)
from app.db import get_session  # noqa: F401
from app.models import ServerVerdict, Base  # type: ignore

router = APIRouter()


@router.get(
    "/servers/{server_id}/verdict_history",
    response_class=JSONResponse,
    response_model=List[dict],
)
def get_verdict_history(
    server_id: str,
    start_date: Optional[datetime] = Query(
        None, description="ISO8601 start date filter (inclusive)"
    ),
    end_date: Optional[datetime] = Query(
        None, description="ISO8601 end date filter (inclusive)"
    ),
    db: Session = Depends(get_session),
):
    """
    Retrieve chronological list of past verdicts for a given MCP server.
    """
    query = db.query(ServerVerdict).filter(ServerVerdict.server_id == server_id)

    if start_date:
        query = query.filter(ServerVerdict.timestamp >= start_date)
    if end_date:
        query = query.filter(ServerVerdict.timestamp <= end_date)

    records = (
        query.order_by(ServerVerdict.timestamp.desc())
        .all()
    )

    if not records:
        raise HTTPException(status_code=404, detail="No verdict history found")

    result = [
        {
            "verdict": rec.verdict,
            "timestamp": rec.timestamp.isoformat(),
            "reason": rec.reason,
        }
        for rec in records
    ]
    return result


# --------------------------------------------------------------------------- #
# Self‑test block
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a temporary in‑memory SQLite DB and bind it to the models
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Dependency override that yields a session from the temporary DB
    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build FastAPI app and inject the router
    app = FastAPI()
    app.include_router(router)

    # Override the production DB dependency with our test DB
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Insert mock verdict rows
    with TestSessionLocal() as db:
        mock_data = [
            {
                "server_id": "srv-123",
                "verdict": "allow",
                "timestamp": datetime(2023, 1, 10, 12, 0, 0),
                "reason": "initial trust",
            },
            {
                "server_id": "srv-123",
                "verdict": "block",
                "timestamp": datetime(2023, 2, 5, 15, 30, 0),
                "reason": "malware detected",
            },
        ]
        for row in mock_data:
            db.add(
                ServerVerdict(
                    server_id=row["server_id"],
                    verdict=row["verdict"],
                    timestamp=row["timestamp"],
                    reason=row["reason"],
                )
            )
        db.commit()

    # Call the endpoint
    response = client.get("/servers/srv-123/verdict_history")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()

    # Expected order: newest first
    expected = [
        {
            "verdict": "block",
            "timestamp": "2023-02-05T15:30:00",
            "reason": "malware detected",
        },
        {
            "verdict": "allow",
            "timestamp": "2023-01-10T12:00:00",
            "reason": "initial trust",
        },
    ]

    assert data == expected, f"Response mismatch: {json.dumps(data, indent=2)}"
    print("PASS")