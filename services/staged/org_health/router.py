from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org

router = APIRouter(prefix="/org_health", tags=["org_health"])


class OrgHealthResponse(BaseModel):
    org_id: int
    org_name: str
    created_at: str
    health_score: float

    class Config:
        from_attributes = True


class OrgHealthSummary(BaseModel):
    total_orgs: int
    avg_health_score: float
    orgs: list[OrgHealthResponse]

    class Config:
        from_attributes = True


@router.get("/orgs", response_model=list[OrgHealthResponse])
def list_org_health(session: Session = Depends(get_session)):
    """List health metrics for all organizations."""
    orgs = session.query(Org).all()
    return [
        OrgHealthResponse(
            org_id=org.id,
            org_name=org.name,
            created_at=org.created_at.isoformat() if org.created_at else None,
            health_score=_compute_health_score(org, session),
        )
        for org in orgs
    ]


@router.get("/summary", response_model=OrgHealthSummary)
def get_health_summary(session: Session = Depends(get_session)):
    """Get summary health metrics across all organizations."""
    orgs = session.query(Org).all()
    org_responses = []
    total_score = 0.0

    for org in orgs:
        score = _compute_health_score(org, session)
        org_responses.append(
            OrgHealthResponse(
                org_id=org.id,
                org_name=org.name,
                created_at=org.created_at.isoformat() if org.created_at else None,
                health_score=score,
            )
        )
        total_score += score

    return OrgHealthSummary(
        total_orgs=len(orgs),
        avg_health_score=total_score / len(orgs) if orgs else 0.0,
        orgs=org_responses,
    )


@router.get("/orgs/{org_id}", response_model=OrgHealthResponse)
def get_org_health(org_id: int, session: Session = Depends(get_session)):
    """Get health metrics for a specific organization."""
    org = session.query(Org).filter(Org.id == org_id).first()
    if not org:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Organization not found")

    return OrgHealthResponse(
        org_id=org.id,
        org_name=org.name,
        created_at=org.created_at.isoformat() if org.created_at else None,
        health_score=_compute_health_score(org, session),
    )


def _compute_health_score(org: Org, session: Session) -> float:
    """Compute a basic health score for an organization."""
    if org.created_at is None:
        return 0.0

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    age_days = (now - org.created_at).days

    # Simple health score based on org age (just a placeholder metric)
    # Real implementation would query associated data for more meaningful metrics
    if age_days < 0:
        return 0.0
    elif age_days < 30:
        return 100.0
    elif age_days < 90:
        return 90.0
    elif age_days < 180:
        return 80.0
    elif age_days < 365:
        return 75.0
    else:
        # Older orgs get a stable score but could decay slightly
        base_score = 70.0
        decay = min((age_days - 365) * 0.01, 20.0)
        return max(base_score - decay, 50.0)


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    print("Starting org_health service on port 8773...")
    uvicorn.run(app, host="0.0.0.0", port=8773)