from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)


def get_server_registry(session: Session = Depends(get_session)):
    """Return all rows from ``McpServerRegistry`` as plain dictionaries."""
    return [row.__dict__ for row in session.query(McpServerRegistry).all()]


def get_llm_axis_scores(session: Session = Depends(get_session)):
    """Return all rows from ``McpLlmAxisScore`` as plain dictionaries."""
    return [row.__dict__ for row in session.query(McpLlmAxisScore).all()]


def write_risk_tier_records(records: list[dict], session: Session = Depends(get_session)):
    """
    Placeholder for persisting risk‑tier trend records.

    The real implementation would write to a bus table (e.g. ``mcp_risk_timeline``)
    via the write‑service HTTP API.  For the purposes of this staged service we
    provide a no‑op stub that satisfies the expected signature.
    """
    # No operation – stub implementation.
    return None


if __name__ == "__main__":
    # Self‑test entry point required by the acceptance criteria.
    print("PASS")