"""
services/staged/registry_heatmap_api/contract.py

FastAPI contract for the Registry Heatmap API.
Mirrors the exemplar contract pattern.
"""

from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import func, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from typing import Generator, List, Dict, Any

# Real data layer imports (must not be re‑implemented)
from app.db import get_session, Base  # get_session provides the app's DB session, Base for table creation
from app.models import McpServerRegistry  # authoritative model


router = APIRouter(prefix="/api")


@router.get(
    "/registry/heatmap",
    response_model=Dict[str, List[Dict[str, Any]]],
    status_code=status.HTTP_200_OK,
)
def get_registry_heatmap(session=Depends(get_session)):
    """
    Returns a heatmap of registry sources with aggregated health metadata.
    """
    try:
        rows = (
            session.query(
                McpServerRegistry.registry_source,
                func.count().label("server_count"),
                func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
                func.avg(McpServerRegistry.confidence).label("avg_confidence"),
                func.max(McpServerRegistry.last_scanned).label("last_scanned"),
            )
            .group_by(McpServerRegistry.registry_source)
            .all()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    sources = [
        {
            "source": row.registry_source,
            "server_count": row.server_count,
            "avg_trust_score": float(row.avg_trust_score) if row.avg_trust_score is not None else None,
            "avg_confidence": float(row.avg_confidence) if row.avg_confidence is not None else None,
            "last_scanned": row.last_scanned.isoformat() if row.last_scanned else None,
        }
        for row in rows
    ]

    return {"sources": sources}


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.registry_heatmap_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a minimal FastAPI app for testing
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (StaticPool) – overrides the real DB session
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables using the real Base metadata
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Generator:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed test data: 3 sources, 5 servers each
    # ------------------------------------------------------------------- #
    test_sources = ["sourceA", "sourceB", "sourceC"]
    servers = []
    for src in test_sources:
        for i in range(5):
            servers.append(
                McpServerRegistry(
                    server_id=f"{src}_srv_{i}",
                    name=f"{src} Server {i}",
                    registry_source=src,
                    trust_score=80 + i,          # arbitrary values
                    confidence=0.9 - i * 0.1,
                    last_scanned=None,           # can be null for test
                )
            )

    # Insert seed data
    with TestSessionLocal() as sess:
        sess.add_all(servers)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Execute test client request
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/registry/heatmap")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    data = response.json()
    assert "sources" in data, "Missing 'sources' key in response"
    sources = data["sources"]
    assert isinstance(sources, list), "'sources' should be a list"
    assert len(sources) == 3, f"Expected 3 sources, got {len(sources)}"

    # Verify that at least one source reports the correct server count (5)
    counts = {s["source"]: s["server_count"] for s in sources}
    assert any(count == 5 for count in counts.values()), "No source reported server_count == 5"

    print("PASS")
    raise SystemExit(0)