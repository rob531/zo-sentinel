from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.db import get_session
from app.models import PerspectiveSnapshot
from sqlalchemy.orm import Session
from sqlalchemy import select

router = APIRouter(prefix="", tags=["perspective_membership"])


class ServerMembership(BaseModel):
    perspective_id: str
    server_id: str
    server_name: str
    org_id: str
    risk_tier: str
    overall_score: float

    class Config:
        from_attributes = True


def get_perspective_snapshot(perspective_id: str, session: Session) -> List[PerspectiveSnapshot]:
    stmt = select(PerspectiveSnapshot).where(PerspectiveSnapshot.perspective_id == perspective_id)
    result = session.execute(stmt).scalars().all()
    return list(result)


@router.get("/perspectives/{perspective_id}/membership", response_model=List[ServerMembership])
def get_perspective_membership(
    perspective_id: str,
    session: Session = Depends(get_session),
) -> List[ServerMembership]:
    snapshots = get_perspective_snapshot(perspective_id, session)
    return [
        ServerMembership(
            perspective_id=s.perspective_id,
            server_id=s.server_id,
            server_name=getattr(s, 'server_name', ''),
            org_id=getattr(s, 'org_id', ''),
            risk_tier=getattr(s, 'risk_tier', ''),
            overall_score=getattr(s, 'overall_score', 0.0),
        )
        for s in snapshots
    ]


__all__ = ["router", "get_perspective_snapshot", "get_perspective_membership"]


if __name__ == "__main__":
    import sys
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from pydantic import BaseModel

        class PerspectiveSnapshotModel:
            def __init__(self, perspective_id: str, server_id: str, **kwargs):
                self.perspective_id = perspective_id
                self.server_id = server_id
                for k, v in kwargs.items():
                    setattr(self, k, v)

        test_snapshots = [
            PerspectiveSnapshotModel(
                perspective_id="test-persp-1",
                server_id="srv-001",
                server_name="Production API",
                org_id="org-abc",
                risk_tier="high",
                overall_score=72.5,
            ),
            PerspectiveSnapshotModel(
                perspective_id="test-persp-1",
                server_id="srv-002",
                server_name="Staging DB",
                org_id="org-abc",
                risk_tier="medium",
                overall_score=85.0,
            ),
            PerspectiveSnapshotModel(
                perspective_id="test-persp-2",
                server_id="srv-003",
                server_name="Dev Gateway",
                org_id="org-xyz",
                risk_tier="low",
                overall_score=91.0,
            ),
        ]

        def override_get_session():
            class FakeResult:
                def scalars(self):
                    return self
                def all(self):
                    return [s for s in test_snapshots if s.perspective_id == "test-persp-1"]
            class FakeSession:
                def execute(self, stmt):
                    return FakeResult()
            return FakeSession()

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        app.dependency_overrides[get_session] = override_get_session

        response = client.get("/perspectives/test-persp-1/membership")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) == 2, f"Expected 2 servers, got {len(data)}"
        assert data[0]["server_id"] == "srv-001"
        assert data[1]["server_id"] == "srv-002"
        assert data[0]["perspective_id"] == "test-persp-1"
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)