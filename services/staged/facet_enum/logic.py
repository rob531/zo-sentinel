# services/staged/facet_enum/logic.py
from typing import List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class Facet(BaseModel):
    axis_name: str
    label: str
    label_index: int


class FacetEnumResponse(BaseModel):
    facets: List[Facet]


def get_facet_enum(session: Session = Depends(get_session)) -> FacetEnumResponse:
    rows = (
        session.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
        .distinct()
        .all()
    )
    facets = [Facet(axis_name=r[0], label=r[1], label_index=r[2]) for r in rows]
    return FacetEnumResponse(facets=facets)


if __name__ == "__main__":
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB using the real models
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.db import Base  # noqa: E402

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Populate test data
    session = SessionLocal()
    test_rows = [
        McpLlmAxisScore(axis_name="axis1", label="A", label_index=0),
        McpLlmAxisScore(axis_name="axis1", label="B", label_index=1),
        McpLlmAxisScore(axis_name="axis2", label="C", label_index=0),
        McpLlmAxisScore(axis_name="axis1", label="A", label_index=0),  # duplicate
    ]
    session.add_all(test_rows)
    session.commit()

    # Run the logic directly (no FastAPI dependency injection needed here)
    result = get_facet_enum(session)

    # Assertions per acceptance criteria
    assert isinstance(result, FacetEnumResponse)
    assert len(result.facets) == 3  # distinct rows
    axis_names = {f.axis_name for f in result.facets}
    assert "axis1" in axis_names and "axis2" in axis_names
    labels = {f.label for f in result.facets}
    assert "A" in labels

    print("PASS")