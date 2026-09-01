from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_facet_enum

router = APIRouter(prefix="/api")


@router.get("/facets/enum")
def facet_enum(session: Session = Depends(get_session)):
    """Return distinct facet enumeration."""
    return get_facet_enum(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the real model so the test uses the same table definition
    from app.models import Base, McpLlmAxisScore

    # Create an in‑memory SQLite engine and bind a sessionmaker to it
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Dependency override that yields a session bound to the in‑memory DB
    def override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Populate the temporary database with test data
    with TestSessionLocal() as db:
        db.add_all(
            [
                McpLlmAxisScore(axis_name="axis1", label="A", label_index=0),
                McpLlmAxisScore(axis_name="axis1", label="B", label_index=1),
                McpLlmAxisScore(axis_name="axis2", label="C", label_index=0),
                McpLlmAxisScore(axis_name="axis2", label="D", label_index=1),
            ]
        )
        db.commit()

    # Build FastAPI app and inject the override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Perform request and validate response
    resp = client.get("/api/facets/enum")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    facets = data.get("facets", [])
    assert len(facets) == 4, f"Expected 4 facets, got {len(facets)}"
    axis_names = {f["axis_name"] for f in facets}
    assert len(axis_names) == 2, f"Expected 2 distinct axes, got {len(axis_names)}"
    assert any(f["label"] == "A" for f in facets), "Label 'A' not found in facets"

    print("PASS")