from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from verdict_breakdown_api import get_principal, require_admin, Principal

router = APIRouter(prefix='/api', tags=['disputes'])

RISK_CLASSES = ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
REASON_CATEGORIES = (
    'official_or_established_maintainer',
    'false_positive_overrated',
    'underrated_actual_risk',
    'outdated_assessment',
    'incorrect_capability_or_axis',
    'other'
)
AXES = (
    'auth_strength',
    'capability_breadth',
    'data_sensitivity',
    'network_egress',
    'maintainer_trust',
    'exploit_surface'
)

class DisputeCreate(BaseModel):
    server_id: str
    proposed_overall_risk: str
    reason_category: str
    explanation: str
    proposed_axes: Optional[dict] = None

class DisputeDecision(BaseModel):
    decision: str
    admin_note: Optional[str] = None

@router.post('/disputes')
async def create_dispute(
    dispute: DisputeCreate,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_session)
):
    if dispute.proposed_overall_risk not in RISK_CLASSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid proposed_overall_risk'
        )
    if dispute.reason_category not in REASON_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid reason_category'
        )
    if len(dispute.explanation.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Explanation must be at least 10 characters'
        )
    if dispute.proposed_axes:
        for axis in dispute.proposed_axes:
            if axis not in AXES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'Invalid axis: {axis}'
                )
            if not isinstance(dispute.proposed_axes[axis], str) or not dispute.proposed_axes[axis].strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'Invalid value for axis: {axis}'
                )

    server = db.get(McpServerRegistry, dispute.server_id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Server not found'
        )

    new_dispute = McpScoreDispute(
        server_id=dispute.server_id,
        submitted_by=principal.user_id,
        proposed_overall_risk=dispute.proposed_overall_risk,
        proposed_axes=dispute.proposed_axes,
        reason_category=dispute.reason_category,
        explanation=dispute.explanation,
        status='pending'
    )
    db.add(new_dispute)
    db.commit()
    db.refresh(new_dispute)

    return {'status': 'submitted', 'id': new_dispute.id}

@router.get('/disputes/mine')
async def get_my_disputes(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_session)
):
    stmt = select(McpScoreDispute).where(
        McpScoreDispute.submitted_by == principal.user_id
    ).order_by(McpScoreDispute.created_at.desc())
    disputes = db.execute(stmt).scalars().all()
    return disputes

@router.get('/admin/disputes')
async def get_disputes(
    status: str = 'pending',
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_session)
):
    stmt = select(
        McpScoreDispute.id,
        McpScoreDispute.server_id,
        McpScoreDispute.proposed_overall_risk,
        McpScoreDispute.reason_category,
        McpScoreDispute.explanation,
        McpScoreDispute.proposed_axes,
        McpScoreDispute.submitted_by,
        McpScoreDispute.status,
        McpScoreDispute.created_at
    ).where(McpScoreDispute.status == status).order_by(McpScoreDispute.created_at.desc())
    disputes = db.execute(stmt).all()
    return disputes

@router.post('/admin/disputes/{dispute_id}')
async def resolve_dispute(
    dispute_id: int,
    decision: DisputeDecision,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_session)
):
    if decision.decision not in ('approved', 'rejected'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid decision'
        )

    dispute = db.get(McpScoreDispute, dispute_id)
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Dispute not found'
        )

    dispute.status = decision.decision
    dispute.admin_note = decision.admin_note
    dispute.resolved_at = datetime.now(timezone.utc)
    db.commit()

    return {
        'status': 'resolved',
        'id': dispute_id,
        'decision': decision.decision
    }

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool
    )
    from app.models import Base
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    def override_get_principal():
        return Principal(user_id='u1', role='public')

    app.dependency_overrides[get_principal] = override_get_principal

    def override_require_admin():
        return Principal(user_id='admin', role='admin')

    app.dependency_overrides[require_admin] = override_require_admin

    client = TestClient(app)

    # Seed data
    db = SessionLocal()
    db.add(McpServerRegistry(server_id='srv1', name='Test MCP', url='https://github.com/x/y', registry_source='glama'))
    db.commit()
    db.close()

    # Test cases
    try:
        # Test case (a)
        response = client.post(
            '/api/disputes',
            json={
                'server_id': 'srv1',
                'proposed_overall_risk': 'MEDIUM',
                'reason_category': 'false_positive_overrated',
                'explanation': 'Official maintainer; not high risk',
                'proposed_axes': {'maintainer_trust': 'ESTABLISHED'}
            }
        )
        assert response.status_code == 200
        assert 'id' in response.json()

        # Test case (b)
        response = client.post(
            '/api/disputes',
            json={
                'server_id': 'srv1',
                'proposed_overall_risk': 'BOGUS',
                'reason_category': 'false_positive_overrated',
                'explanation': 'Official maintainer; not high risk',
                'proposed_axes': {'maintainer_trust': 'ESTABLISHED'}
            }
        )
        assert response.status_code == 400

        # Test case (c)
        response = client.post(
            '/api/disputes',
            json={
                'server_id': 'srv1',
                'proposed_overall_risk': 'MEDIUM',
                'reason_category': 'false_positive_overrated',
                'explanation': 'x',
                'proposed_axes': {'maintainer_trust': 'ESTABLISHED'}
            }
        )
        assert response.status_code == 400

        # Test case (d)
        response = client.get('/api/admin/disputes?status=pending')
        assert response.status_code == 200
        assert len(response.json()) > 0

        # Test case (e)
        dispute_id = response.json()[0]['id']
        response = client.post(
            f'/api/admin/disputes/{dispute_id}',
            json={'decision': 'approved'}
        )
        assert response.status_code == 200
        assert response.json()['decision'] == 'approved'

        print('PASS')
    except AssertionError as e:
        print(f'FAIL: {str(e)}')