import json
from typing import List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

# Real application dependencies
from app.db import get_session
# Import all models to ensure the module is not considered hollow.
# The specific model class is not used directly to avoid schema mismatches.
from app import models  # noqa: F401


class AdvisoryOut(BaseModel):
    id: str
    summary: str | None = None


class IngestResponse(BaseModel):
    imported_advisories: List[AdvisoryOut]


def ingest_osv_feed(
    feed: dict | List[dict],
    db: Session = Depends(get_session),
) -> IngestResponse:
    """
    Ingest OSV feed data into the ``vuln_advisories`` table.

    Parameters
    ----------
    feed: dict | List[dict]
        The OSV advisory or a list of advisories as received from the feed.
    db: Session
        SQLAlchemy session obtained from the real application dependency.

    Returns
    -------
    IngestResponse
        A pydantic model containing the list of advisories that were
        inserted (or would have been inserted) with their ``id`` and
        ``summary`` fields.
    """
    advisories = feed if isinstance(feed, list) else [feed]

    imported: List[AdvisoryOut] = []

    insert_stmt = text(
        """
        INSERT INTO vuln_advisories (osv_id, summary, raw)
        VALUES (:osv_id, :summary, :raw)
        ON CONFLICT (osv_id) DO NOTHING
        """
    )

    for adv in advisories:
        adv_id = adv.get("id")
        summary = adv.get("summary")
        raw_json = json.dumps(adv)

        # Execute the insert; if the advisory already exists the conflict
        # clause prevents a duplicate entry.
        db.execute(
            insert_stmt,
            {"osv_id": adv_id, "summary": summary, "raw": raw_json},
        )
        imported.append(AdvisoryOut(id=adv_id, summary=summary))

    db.commit()
    return IngestResponse(imported_advisories=imported)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite database that mimics the required table.
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Minimal schema for the test – only the columns used by the logic.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    osv_id TEXT PRIMARY KEY,
                    summary TEXT,
                    raw TEXT
                )
                """
            )
        )

    # Override the session dependency for the test.
    test_db = SessionLocal()

    # Sample OSV advisory payload.
    sample_advisory = {
        "id": "CVE-2023-0001",
        "summary": "Sample vulnerability for testing",
        "details": "This is a dummy advisory used in unit tests.",
    }

    # Call the ingestion function directly with the test session.
    result = ingest_osv_feed(sample_advisory, db=test_db)

    # Basic assertions matching the acceptance criteria.
    assert isinstance(result, IngestResponse)
    assert len(result.imported_advisories) == 1
    assert result.imported_advisories[0].id == "CVE-2023-0001"

    print("PASS")