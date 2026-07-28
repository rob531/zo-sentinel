from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_score_change_timeline

router = APIRouter(prefix="/api")


@router.get(
    "/scoring/timeline",
    response_model=dict,
    summary="Score change timeline for a server",
)
def scoring_timeline(
    server_id: int = Query(..., description="Identifier of the server"),
    days: int = Query(30, ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
):
    """
    Return a timeline of axis score changes for the given server over the past *days*.
    """
    try:
        return get_score_change_timeline(db, server_id, days)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import DateTime, create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    # --------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and populate it with minimal seed data
    # --------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    # Helper to build a dict that only contains columns actually present on a model
    def filter_kwargs(model, data):
        valid = {c.name for c in model.__table__.columns}
        return {k: v for k, v in data.items() if k in valid}

    # Insert two server registry rows
    with SessionLocal() as sess:
        reg_kwargs = filter_kwargs(
            McpServerRegistry,
            {
                "server_id": 1,
                "historical_tier": json.dumps(
                    [{"date": "2023-01-01", "tier": "low"}]
                ),
            },
        )
        sess.add(McpServerRegistry(**reg_kwargs))

        reg_kwargs = filter_kwargs(McpServerRegistry, {"server_id": 2})
        sess.add(McpServerRegistry(**reg_kwargs))
        sess.commit()

    # Determine which column stores the timestamp for axis scores
    date_column = next(
        (
            c.name
            for c in McpLlmAxisScore.__table__.columns
            if isinstance(c.type, DateTime)
        ),
        None,
    )
    if date_column is None:
        raise RuntimeError("No DateTime column found on McpLlmAxisScore")

    # Insert axis scores for server 1 across three consecutive days
    base = datetime(2023, 1, 1)
    scores = [
        {
            "server_id": 1,
            "axis": "axisA",
            "p_top": 0.10,
            date_column: base,
        },
        {
            "server_id": 1,
            "axis": "axisA",
            "p_top": 0.30,
            date_column: base + timedelta(days=1),
        },
        {
            "server_id": 1,
            "axis": "axisA",
            "p_top": 0.55,
            date_column: base + timedelta(days=2),
        },
    ]

    with SessionLocal() as sess:
        for row in scores:
            sess.add(McpLlmAxisScore(**filter_kwargs(McpLlmAxisScore, row)))
        sess.commit()

    # --------------------------------------------------------------------- #
    # Build FastAPI app, override the DB dependency, and run the test client
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    response = client.get(
        "/api/scoring/timeline", params={"server_id": 1, "days": 3}
    )
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["server_id"] == 1
    assert payload["days"] == 3
    changes = payload.get("changes", [])
    assert len(changes) >= 1, "No changes returned"

    # Verify that the known delta (0.20) appears in the result set
    known_delta = 0.20
    assert any(
        abs(item.get("delta", 0) - known_delta) < 1e-6 for item in changes
    ), f"Expected delta {known_delta} not found"

    print("PASS")