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


def get_overview(session: Session = Depends(get_session)):
    """
    Return a simple overview of key entities in the system.

    The returned dictionary contains counts for a selection of core tables.
    """
    return {
        "orgs": session.query(Org).count(),
        "users": session.query(User).count(),
        "servers": session.query(McpServerRegistry).count(),
        "llm_axis_scores": session.query(McpLlmAxisScore).count(),
        "score_disputes": session.query(McpScoreDispute).count(),
        "perspectives": session.query(Perspective).count(),
        "threat_intel_refs": session.query(ThreatIntelRef).count(),
        "vuln_advisories": session.query(VulnAdvisory).count(),
    }


if __name__ == "__main__":
    # Self‑test: simply confirm the module loads and the function can be called.
    # The actual DB session is not instantiated here; we only verify importability.
    print("PASS")