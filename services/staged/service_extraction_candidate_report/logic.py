# services/staged/service_extraction_candidate_report/logic.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sqlalchemy import text
from sqlalchemy.orm import Session as ORMSession

from app.db import get_session, Base  # Base is needed for the __main__ test
from app.models import McpServerRegistry  # real model – must contain the columns used below


router = APIRouter()


class Candidate(BaseModel):
    router_name: str
    declared_in: str
    last_modified: datetime


class Report(BaseModel):
    service_extraction_candidates: List[Candidate]


def _fetch_candidates(db: ORMSession) -> List[dict]:
    """
    Return a list of routers that are declared but never mounted.
    The underlying table is ``McpServerRegistry`` and must expose the
    columns ``router_name``, ``declared_in`` and ``last_modified`` together
    with a boolean ``mounted`` flag.
    """
    stmt = text(
        """
        SELECT
            router_name,
            declared_in,
            last_modified
        FROM McpServerRegistry
        WHERE mounted = false
        """
    )
    rows = db.execute(stmt).mappings()
    return [
        {
            "router_name": row["router_name"],
            "declared_in": row["declared_in"],
            "last_modified": row["last_modified"],
        }
        for row in rows
    ]


@router.get(
    "/api/reports/service-extraction-candidates",
    response_model=Report,
    tags=["reports"],
)
def get_service_extraction_candidate_report(
    db: ORMSession = Depends(get_session),
) -> Report:
    candidates = _fetch_candidates(db)
    return Report(service_extraction_candidates=candidates)


# --------------------------------------------------------------------------- #
# Self‑test (executed only when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The test creates an in‑memory SQLite DB, populates it with three routers
    # (two mounted, one candidate) and verifies the endpoint logic.
    from sqlalchemy import Column, Boolean, DateTime, String
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine

    # ------------------------------------------------------------------- #
    # Define a temporary table that mirrors the real model's schema.
    # This is safe because the production code imports the real model;
    # the test only needs the table to exist in the temporary DB.
    # ------------------------------------------------------------------- #
    class TestMcpServerRegistry(Base):
        __tablename__ = "McpServerRegistry"

        router_name = Column(String, primary_key=True)
        declared_in = Column(String, nullable=False)
        last_modified = Column(DateTime, nullable=False)
        mounted = Column(Boolean, nullable=False)

    # Create SQLite in‑memory DB and the table
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Insert test data
    now = datetime.utcnow()
    db.add_all(
        [
            TestMcpServerRegistry(
                router_name="mounted_router_1",
                declared_in="module_a",
                last_modified=now,
                mounted=True,
            ),
            TestMcpServerRegistry(
                router_name="mounted_router_2",
                declared_in="module_b",
                last_modified=now,
                mounted=True,
            ),
            TestMcpServerRegistry(
                router_name="candidate_router",
                declared_in="module_c",
                last_modified=now,
                mounted=False,
            ),
        ]
    )
    db.commit()

    # Override the dependency used by the endpoint
    def get_test_session() -> ORMSession:  # pragma: no cover
        return db

    # Directly call the core function (bypassing FastAPI) for simplicity
    result = _fetch_candidates(db)

    assert isinstance(result, list), "Result must be a list"
    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    candidate = result[0]
    assert candidate["router_name"] == "candidate_router", "Candidate router name mismatch"

    print("PASS")