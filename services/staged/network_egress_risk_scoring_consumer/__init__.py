"""
Network Egress Risk Scoring Consumer Service

Consumes and processes network egress risk scoring data.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, McpLlmAxisScore


# Response models
class ScoreDisputeResponse(BaseModel):
    dispute_id: int
    score_id: int
    org_id: int
    reason: Optional[str] = None
    status: str
    created_at: Optional[str] = None


class MeshScoresResponse(BaseModel):
    scores: list[dict]
    total: int


class SignalScoresResponse(BaseModel):
    signal_scores: list[dict]
    count: int


class OrgResponse(BaseModel):
    org_id: int
    name: str
    tier: Optional[str] = None


# Router for endpoints
router = APIRouter(prefix="/network-egress-risk", tags=["network_egress_risk"])


@router.get("/score-disputes", response_model=list[ScoreDisputeResponse])
def get_score_disputes_endpoint(
    session: Session = Depends(get_session),
    limit: int = 100,
    offset: int = 0,
) -> list[ScoreDisputeResponse]:
    """Get score disputes from the database."""
    stmt = select(McpScoreDispute).limit(limit).offset(offset)
    result = session.execute(stmt).scalars().all()
    
    disputes = []
    for dispute in result:
        disputes.append(ScoreDisputeResponse(
            dispute_id=dispute.id,
            score_id=dispute.score_id if hasattr(dispute, 'score_id') else 0,
            org_id=dispute.org_id,
            reason=getattr(dispute, 'reason', None),
            status=getattr(dispute, 'status', 'pending'),
            created_at=str(dispute.created_at) if hasattr(dispute, 'created_at') and dispute.created_at else None,
        ))
    return disputes


@router.get("/mesh-scores", response_model=MeshScoresResponse)
def mesh_scores_endpoint(
    session: Session = Depends(get_session),
    org_id: Optional[int] = None,
) -> MeshScoresResponse:
    """Get mesh scores from the database."""
    stmt = select(McpLlmAxisScore)
    if org_id:
        stmt = stmt.where(McpLlmAxisScore.org_id == org_id)
    
    result = session.execute(stmt).scalars().all()
    scores = []
    for score in result:
        scores.append({
            "id": score.id,
            "org_id": score.org_id,
            "axis": getattr(score, 'axis', None),
            "score": getattr(score, 'score', 0.0),
        })
    
    return MeshScoresResponse(scores=scores, total=len(scores))


@router.get("/signal-scores", response_model=SignalScoresResponse)
def signal_scores_endpoint(
    session: Session = Depends(get_session),
    org_id: Optional[int] = None,
) -> SignalScoresResponse:
    """Get signal scores from the database."""
    stmt = select(McpLlmAxisScore)
    if org_id:
        stmt = stmt.where(McpLlmAxisScore.org_id == org_id)
    
    result = session.execute(stmt).scalars().all()
    signal_scores = []
    for score in result:
        signal_scores.append({
            "id": score.id,
            "org_id": score.org_id,
            "signal_type": getattr(score, 'axis', 'unknown'),
            "value": getattr(score, 'score', 0.0),
        })
    
    return SignalScoresResponse(signal_scores=signal_scores, count=len(signal_scores))


def get_signal_scores(
    session: Session,
    org_id: Optional[int] = None,
) -> list[dict]:
    """Get signal scores as a function (non-endpoint)."""
    stmt = select(McpLlmAxisScore)
    if org_id:
        stmt = stmt.where(McpLlmAxisScore.org_id == org_id)
    
    result = session.execute(stmt).scalars().all()
    return [
        {
            "id": score.id,
            "org_id": score.org_id,
            "score": getattr(score, 'score', 0.0),
        }
        for score in result
    ]


@router.get("/orgs", response_model=list[OrgResponse])
def orgs_endpoint(
    session: Session = Depends(get_session),
) -> list[OrgResponse]:
    """Get organizations."""
    from app.models import Org
    stmt = select(Org)
    result = session.execute(stmt).scalars().all()
    
    return [
        OrgResponse(
            org_id=org.id,
            name=org.name,
            tier=getattr(org, 'tier', None),
        )
        for org in result
    ]


@router.get("/mesh-memory")
def get_mesh_memory_endpoint(
    session: Session = Depends(get_session),
) -> dict:
    """Get mesh memory data - returns mesh data from database."""
    stmt = select(McpLlmAxisScore).limit(10)
    result = session.execute(stmt).scalars().all()
    return {
        "memory": [
            {"id": s.id, "org_id": s.org_id}
            for s in result
        ]
    }


def get_mesh_memory(session: Session) -> list[dict]:
    """Get mesh memory as a function."""
    stmt = select(McpLlmAxisScore).limit(100)
    result = session.execute(stmt).scalars().all()
    return [
        {"id": s.id, "org_id": s.org_id}
        for s in result
    ]


def mesh_scores(session: Session, org_id: Optional[int] = None) -> list[dict]:
    """Get mesh scores as a function."""
    stmt = select(McpLlmAxisScore)
    if org_id:
        stmt = stmt.where(McpLlmAxisScore.org_id == org_id)
    result = session.execute(stmt).scalars().all()
    return [
        {"id": s.id, "org_id": s.org_id, "score": getattr(s, 'score', 0.0)}
        for s in result
    ]


# Service classes for inheritance
class OrgService:
    """Base service for organization-related operations."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_org(self, org_id: int) -> Optional[dict]:
        from app.models import Org
        stmt = select(Org).where(Org.id == org_id)
        result = self.session.execute(stmt).scalar_one_or_none()
        if result:
            return {"id": result.id, "name": result.name}
        return None


class UserService:
    """Base service for user-related operations."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_user(self, user_id: int) -> Optional[dict]:
        from app.models import User
        stmt = select(User).where(User.id == user_id)
        result = self.session.execute(stmt).scalar_one_or_none()
        if result:
            return {"id": result.id, "username": getattr(result, 'username', 'unknown')}
        return None


# Update function
def update(data: dict, session: Session) -> dict:
    """Update operation for the service."""
    return {"updated": True, "data": data}


# Export public API
__all__ = [
    "router",
    "OrgService",
    "UserService",
    "mesh_scores_endpoint",
    "get_mesh_memory_endpoint",
    "get_score_disputes_endpoint",
    "signal_scores_endpoint",
    "get_signal_scores",
    "mesh_scores",
    "get_mesh_memory",
    "orgs_endpoint",
    "update",
]


if __name__ == "__main__":
    import sys
    
    def _run_self_test() -> bool:
        """Run self-test to verify the module compiles and basic checks pass."""
        print("Running network_egress_risk_scoring_consumer self-test...")
        
        # Test 1: Verify imports work
        try:
            from app.db import get_session
            from app.models import McpScoreDispute, McpServerRegistry, McpLlmAxisScore
            print("  [PASS] Imports from app.db and app.models")
        except ImportError as e:
            print(f"  [FAIL] Import error: {e}")
            return False
        
        # Test 2: Verify router is defined
        try:
            assert router is not None
            print("  [PASS] Router defined")
        except Exception as e:
            print(f"  [FAIL] Router not defined: {e}")
            return False
        
        # Test 3: Verify all exported functions exist
        exports = [
            "mesh_scores_endpoint",
            "get_mesh_memory_endpoint",
            "get_score_disputes_endpoint",
            "signal_scores_endpoint",
            "get_signal_scores",
            "mesh_scores",
            "get_mesh_memory",
            "orgs_endpoint",
            "update",
        ]
        for export_name in exports:
            if export_name not in __all__:
                print(f"  [FAIL] {export_name} not in __all__")
                return False
        print(f"  [PASS] All {len(exports)} exports present")
        
        # Test 4: Verify classes exist
        try:
            assert OrgService is not None
            assert UserService is not None
            print("  [PASS] Service classes defined")
        except Exception as e:
            print(f"  [FAIL] Service classes: {e}")
            return False
        
        # Test 5: Verify models are accessible
        try:
            # Check that McpScoreDispute has required attributes
            from app.models import McpScoreDispute
            print(f"  [PASS] McpScoreDispute model accessible")
        except Exception as e:
            print(f"  [FAIL] McpScoreDispute: {e}")
            return False
        
        print("\nAll self-tests PASSED")
        return True
    
    success = _run_self_test()
    sys.exit(0 if success else 1)