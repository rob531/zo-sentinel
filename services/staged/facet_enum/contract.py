# services/staged/facet_enum/contract.py
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports (must remain unchanged for production)
from app.db import get_session
from app.models import McpLlmAxisScore  # type: ignore

router = APIRouter(prefix="/api")


class Facet(BaseModel):
    axis_name: str
    label: str
    label_index: int


class FacetEnumResponse(BaseModel):
    facets: List[Facet]


@router.get(
    "/facets/enum",
    response_model=FacetEnumResponse,
    tags=["facet_enum"],
    summary="Return distinct facet enumerations",
)
def get_facet_enum(session: Session = Depends(get_session)):
    stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
        .distinct()
        .order_by(McpLlmAxisScore.axis_name)
    )
    rows = session.execute(stmt).all()
    facets = [
        Facet(axis_name=row[0], label=row[1], label_index=row[2]) for row in rows
    ]
    return FacetEnumResponse(facets=facets)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.facet_enum.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Import the declarative base to create tables in the temporary SQLite DB
    from app.models import Base  # type: ignore

    # Build a throwaway in‑memory SQLite engine
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    # Prepare a session factory bound to the in‑memory engine
    TestSession = sessionmaker(bind=engine)

    # Seed data: 4 rows across 2 distinct axes
    test_session: Session = TestSession()
    test_session.add_all(
        [
            McpLlmAxisScore(axis_name="security", label="high", label_index=1),
            McpLlmAxisScore(axis_name="security", label="low", label_index=0),
            McpLlmAxisScore(axis_name="performance", label="fast", label_index=2),
            McpLlmAxisScore(axis_name="performance", label="slow", label_index=3),
        ]
    )
    test_session.commit()
    test_session.close()

    # Override the dependency to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/facets/enum")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    facets = data.get("facets", [])
    # Expect exactly 2 distinct axes
    axes = {f["axis_name"] for f in facets}
    if len(axes) != 2:
        print(f"FAIL: expected 2 axes, got {len(axes)}", file=sys.stderr)
        sys.exit(1)

    # Known label check
    if not any(f["label"] == "high" for f in facets):
        print("FAIL: expected label 'high' not found", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)