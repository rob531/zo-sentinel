from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_growth_progress

router = APIRouter(prefix="/api", tags=["registry_growth_progress"])


@router.get("/registry/growth")
def registry_growth(session: Session = Depends(get_session)):
    return get_registry_growth_progress(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import McpServerRegistry

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the app models
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # Populate the test DB with 10 servers spread over 3 distinct dates
    # ------------------------------------------------------------------- #
    test_session = SessionLocal()
    dates = [
        datetime.date(2023, 1, 1),
        datetime.date(2023, 1, 2),
        datetime.date(2023, 1, 3),
    ]
    for i in range(10):
        server = McpServerRegistry(first_seen=dates[i % 3])
        test_session.add(server)
    test_session.commit()

    # ------------------------------------------------------------------- #
    # Override the FastAPI dependency to use the test session
    # ------------------------------------------------------------------- #
    def get_test_session():
        try:
            yield test_session
        finally:
            pass

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the contract
    # ------------------------------------------------------------------- #
    response = client.get("/api/registry/growth")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    # Expect payload of the form {"dates": [{"date": "...", "count": ...}, ...]}
    date_counts = {item["date"]: item["count"] for item in payload.get("dates", [])}
    assert date_counts.get("2023-01-01") == 4, "Count for 2023-01-01 should be 4"
    print("PASS")