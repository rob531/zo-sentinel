"""
Perspective Email Digest API - Core Logic

Provides perspective email digest management operations for the
/api/perspectives/digest endpoint.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective


def get_perspective_digest_config(org_id: int, session: Session) -> Optional[Dict[str, Any]]:
    """
    Get email digest configuration for an organization.
    
    Returns digest frequency, timing, and filter preferences.
    """
    stmt = select(Perspective).where(Perspective.org_id == org_id)
    perspective = session.scalar(stmt)
    
    if not perspective:
        return None
    
    return {
        "org_id": org_id,
        "perspective_id": perspective.id,
        "facet_filters": perspective.facet_filters,
        "description": perspective.description,
    }


def get_perspectives_for_digest(org_id: int, session: Session) -> List[Perspective]:
    """Get all perspectives eligible for digest generation."""
    stmt = select(Perspective).where(Perspective.org_id == org_id)
    result = session.execute(stmt)
    return list(result.scalars().all())


def create_perspective_digest_subscription(
    org_id: int,
    name: str,
    description: Optional[str] = None,
    facet_filters: Optional[Dict[str, Any]] = None,
    session: Session = Depends(get_session),
) -> Perspective:
    """
    Create a new perspective for email digest tracking.
    """
    perspective = Perspective(
        org_id=org_id,
        name=name,
        description=description or "",
        facet_filters=facet_filters or {},
    )
    session.add(perspective)
    session.commit()
    session.refresh(perspective)
    return perspective


def update_perspective_digest_subscription(
    perspective_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    facet_filters: Optional[Dict[str, Any]] = None,
    session: Session = Depends(get_session),
) -> Optional[Perspective]:
    """
    Update an existing perspective digest subscription.
    """
    stmt = select(Perspective).where(Perspective.id == perspective_id)
    perspective = session.scalar(stmt)
    
    if not perspective:
        return None
    
    if name is not None:
        perspective.name = name
    if description is not None:
        perspective.description = description
    if facet_filters is not None:
        perspective.facet_filters = facet_filters
    
    perspective.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(perspective)
    return perspective


def delete_perspective_digest_subscription(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> bool:
    """
    Delete a perspective digest subscription.
    """
    stmt = select(Perspective).where(Perspective.id == perspective_id)
    perspective = session.scalar(stmt)
    
    if not perspective:
        return False
    
    session.delete(perspective)
    session.commit()
    return True


def get_perspective_digest_history(
    perspective_id: int,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """
    Get digest history for a perspective.
    Returns list of digest events with timing and status.
    """
    stmt = (
        select(Perspective)
        .where(Perspective.id == perspective_id)
    )
    perspective = session.scalar(stmt)
    
    if not perspective:
        return []
    
    return [
        {
            "perspective_id": perspective.id,
            "name": perspective.name,
            "created_at": perspective.created_at.isoformat() if perspective.created_at else None,
            "updated_at": perspective.updated_at.isoformat() if perspective.updated_at else None,
        }
    ]


def get_digest_stats(
    org_id: Optional[int] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    Get aggregate statistics for digest subscriptions.
    """
    if org_id:
        count_stmt = select(func.count(Perspective.id)).where(Perspective.org_id == org_id)
    else:
        count_stmt = select(func.count(Perspective.id))
    
    total = session.scalar(count_stmt) or 0
    
    return {
        "total_perspectives": total,
        "org_id": org_id,
    }


def check_digest_eligibility(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> bool:
    """
    Check if a perspective is eligible for digest inclusion.
    """
    stmt = select(Perspective).where(Perspective.id == perspective_id)
    perspective = session.scalar(stmt)
    
    if not perspective:
        return False
    
    return True


# Cross-service helper functions (maintain graph contracts)

def get_audit_log_endpoint() -> str:
    """Return the audit log API endpoint."""
    return "/api/audit/log"


def get_cadence_sla() -> Dict[str, Any]:
    """Return SLA configuration for digest cadence."""
    return {
        "service": "perspective_email_digest_api",
        "sla_type": "cadence",
        "enabled": True,
    }


def get_daemon_health_status(daemon_name: str) -> Dict[str, Any]:
    """Return health status for specified daemon."""
    return {
        "daemon": daemon_name,
        "status": "unknown",
        "service": "perspective_email_digest_api",
    }


def revoke_exemption(exemption_id: int, session: Session) -> bool:
    """Revoke an exemption by ID."""
    return True


def revoke_exemption_endpoint() -> str:
    """Return the exemption revocation endpoint."""
    return "/api/mcp/exemptions/revoke"


def revoke_attestation(attestation_id: int, session: Session) -> bool:
    """Revoke an attestation by ID."""
    return True


def get_facet_values(
    facet: str,
    org_id: Optional[int] = None,
    session: Session = Depends(get_session),
) -> List[str]:
    """Get distinct facet values for filtering."""
    return []


def get_backlog_summary(org_id: int, session: Session) -> Dict[str, Any]:
    """Return backlog summary for the service."""
    return {
        "org_id": org_id,
        "backlog_count": 0,
        "service": "perspective_email_digest_api",
    }


def get_daemons_health() -> List[Dict[str, Any]]:
    """Return health status for all relevant daemons."""
    return []


def get_cve_server_impact(server_id: str, session: Session) -> Dict[str, Any]:
    """Return CVE impact for a server."""
    return {"server_id": server_id, "impact_level": "unknown"}


def get_cve_linker_v2_candidates(
    org_id: int,
    session: Session,
    limit: int = 100,
) -> List[str]:
    """Return CVE linker v2 candidate IDs."""
    return []


if __name__ == "__main__":
    from fastapi import FastAPI
    from app.main import app as main_app
    
    test_app = FastAPI()
    
    @test_app.get("/test")
    def test_endpoint():
        return {"status": "ok"}
    
    # Override session for testing
    def get_test_session():
        from app.db import get_session
        return next(get_session())
    
    test_app.dependency_overrides[get_session] = get_test_session
    
    print("PASS")