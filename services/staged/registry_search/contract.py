# services/staged/registry_search/contract.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter()


class RegistrySearchResult(BaseModel):
    server_id: int
    name: str
    risk_tier: str
    verdict: str


class RegistrySearchResponse(BaseModel):
    results: List[RegistrySearchResult]


@router.get(
    "/api/registry/search",
    response_model=RegistrySearchResponse,
    summary="Search the server registry",
)
def search_registry(
    q: str,
    session: Session = Depends(get_session),
):
    """Search the `McpServerRegistry` table for servers whose name or description
    contains the supplied query string (case‑insensitive)."""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    stmt = (
        session.query(McpServerRegistry)
        .filter(
            or_(
                McpServerRegistry.name.ilike(f"%{q}%"),
                McpServerRegistry.description.ilike(f"%{q}%"),
            )
        )
        .order_by(McpServerRegistry.server_id)
    )
    rows = stmt.all()

    results = [
        RegistrySearchResult(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            verdict=row.verdict,
        )
        for row in rows
    ]
    return RegistrySearchResponse(results=results)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_search.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the FastAPI dependency.
    # ------------------------------------------------------------------- #
    SQLITE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def _override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Seed the in‑memory DB with deterministic test data.
    # ------------------------------------------------------------------- #
    with SessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id=1,
                    name="Alpha Server",
                    description="First test server",
                    risk_tier="high",
                    verdict="malicious",
                ),
                McpServerRegistry(
                    server_id=2,
                    name="Beta Server",
                    description="Second test server",
                    risk_tier="low",
                    verdict="benign",
                ),
                McpServerRegistry(
                    server_id=3,
                    name="Gamma Node",
                    description="Contains Alpha in description",
                    risk_tier="medium",
                    verdict="suspicious",
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Run the test client against the endpoint.
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    # Query that should match the first and third records (case‑insensitive)
    resp = client.get("/api/registry/search", params={"q": "alpha"})
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    expected = {
        "results": [
            {
                "server_id": 1,
                "name": "Alpha Server",
                "risk_tier": "high",
                "verdict": "malicious",
            },
            {
                "server_id": 3,
                "name": "Gamma Node",
                "risk_tier": "medium",
                "verdict": "suspicious",
            },
        ]
    }

    if resp.json() != expected:
        print(f"FAIL: Unexpected response {resp.json()}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)