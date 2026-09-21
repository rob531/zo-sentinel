from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Optional
from datetime import datetime

from app.db import get_session
from app.models import Perspective, PerspectiveMembership
from app.cache import cache

def get_perspective_metrics(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> Dict[str, Optional[any]]:
    """Fetch membership metrics for a given perspective."""
    perspective = session.query(Perspective).filter_by(id=perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Get member count
    member_count = session.query(PerspectiveMembership).filter_by(perspective_id=perspective_id).count()

    # Get last updated timestamp
    last_membership = session.query(PerspectiveMembership).filter_by(perspective_id=perspective_id).order_by(PerspectiveMembership.created_at.desc()).first()
    last_updated = last_membership.created_at if last_membership else None

    # Get top members (top 5 by created_at)
    top_members = session.query(PerspectiveMembership.user_id).filter_by(perspective_id=perspective_id).order_by(PerspectiveMembership.created_at.desc()).limit(5).all()
    top_members = [member.user_id for member in top_members]

    return {
        "member_count": member_count,
        "last_updated": last_updated,
        "top_members": top_members
    }

@cache(key_prefix="perspective_metrics")
def get_cached_perspective_metrics(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> Dict[str, Optional[any]]:
    """Cached version of get_perspective_metrics."""
    return get_perspective_metrics(perspective_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    # FU-369: `app.dependency_overrides` is not a module in this repo, so the import
    # that stood here raised ModuleNotFoundError the moment this block ran. The
    # override is defined locally instead, per the pattern in
    # services/active/cadence_job_sla_report/contract.py.
    from sqlalchemy import create_engine as _fu369_create_engine
    from sqlalchemy.orm import sessionmaker as _fu369_sessionmaker

    _FU369Session = _fu369_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_fu369_create_engine("sqlite:///:memory:"),
    )


    def _fu369_session_override(session_factory=None):
        """Test session override covering every call shape used in this repo.

        Called with a sessionmaker it returns a dependency callable bound to that
        factory; called with nothing it returns a Session, which is what a FastAPI
        dependency override needs AND what `with ... as session:` needs, because
        Session implements the context-manager protocol itself.
        """
        if session_factory is not None:
            return lambda: session_factory()
        return _FU369Session()

    # Override the session for testing
    app.dependency_overrides[get_session] = _fu369_session_override

    client = TestClient(app)

    # Test with a valid perspective ID
    response = client.get("/api/perspectives/1/metrics")
    assert response.status_code == 200
    assert "member_count" in response.json()
    assert "last_updated" in response.json()
    assert "top_members" in response.json()

    # Test with an invalid perspective ID
    response = client.get("/api/perspectives/999999/metrics")
    assert response.status_code == 404

    # Test cache hit
    response1 = client.get("/api/perspectives/1/metrics")
    response2 = client.get("/api/perspectives/1/metrics")
    assert response1.json() == response2.json()

    print("PASS")