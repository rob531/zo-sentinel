# deps: fastapi
"""FastAPI router exposing GET `/api/mcp_llm_axis_scores/distribution`.

Returns a JSON object mapping each `axis_name` to a list of `p_top` scores
for that axis across all servers (latest model version per server).

The endpoint uses the shared SQLAlchemy session (`app.db.get_session`) and the
ORM model `McpLlmAxisScore` defined in `app.models`. It is pure database
access – no external network calls – and therefore safe for the CI self‑test.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/mcp_llm_axis_scores", tags=["mcp_llm_axis_scores"])

@router.get("/distribution", response_model=Dict[str, List[float]])
def get_distribution(db: Session = Depends(get_session)) -> Dict[str, List[float]]:
    """Return the distribution of `p_top` scores for each axis.

    For each distinct ``axis_name`` we collect all ``p_top`` values from the
    latest ``model_version`` per ``server_id``. The query is performed entirely
    in PostgreSQL‑compatible SQL (SQLAlchemy generates portable statements).
    """
    # Subquery to get the latest model_version per server_id
    sub_latest = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Join back to the main table to keep rows that belong to the latest score
    stmt = (
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.p_top)
        .join(
            sub_latest,
            (McpLlmAxisScore.server_id == sub_latest.c.server_id)
            & (McpLlmAxisScore.scored_at == sub_latest.c.max_scored_at),
        )
    )

    results = db.execute(stmt).all()
    if not results:
        raise HTTPException(status_code=404, detail="No axis scores found")

    distribution: Dict[str, List[float]] = {}
    for axis, p_top in results:
        # ``p_top`` may be NULL; skip those entries
        if p_top is None:
            continue
        distribution.setdefault(axis, []).append(p_top)
    return distribution

# ---------------------------------------------------------------------------
# Self‑test (executed when running the module directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # In‑memory SQLite engine – compatible with the ORM definitions
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed a few rows for each axis
    def _seed():
        s = SessionLocal()
        # One server with a full set of axes
        server_id = "srv_test"
        model_version = "v1.0"
        axes = [
            ("overall_risk", 0.9),
            ("auth_strength", 0.75),
            ("capability_breadth", 0.6),
            ("data_sensitivity", 0.8),
            ("network_egress", 0.4),
            ("maintainer_trust", 0.7),
            ("exploit_surface", 0.5),
        ]
        for i, (axis, p) in enumerate(axes, start=1):
            s.add(
                McpLlmAxisScore(
                    id=i,
                    server_id=server_id,
                    axis_name=axis,
                    p_top=p,
                    model_version=model_version,
                )
            )
        s.commit()
        s.close()

    _seed()

    app = FastAPI()
    app.include_router(router)

    # Dependency override to use the SQLite session factory
    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/api/mcp_llm_axis_scores/distribution")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Expect all defined axes present with non‑empty lists
    expected_axes = {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }
    assert set(data.keys()) == expected_axes, f"Missing or extra axes: {data.keys()}"
    for axis, values in data.items():
        assert isinstance(values, list) and len(values) > 0, f"Empty list for {axis}"
    print("PASS")
