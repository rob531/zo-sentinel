from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db import get_session
from app.models import PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_snapshot_comparison"])


class SnapshotDifference(BaseModel):
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]


class DifferencesResponse(BaseModel):
    differences: List[SnapshotDifference]


@router.get("/perspectives/{perspective_id}/compare/{snapshot_id1}/{snapshot_id2}", response_model=DifferencesResponse)
async def compare_snapshots(
    perspective_id: int,
    snapshot_id1: int,
    snapshot_id2: int,
    db: Session = Depends(get_session)
) -> DifferencesResponse:
    """
    Compare two snapshots from perspective_snapshots table for the given perspective_id
    and return differences in server tiers.
    """
    snapshot1 = db.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.id == snapshot_id1,
        PerspectiveSnapshot.perspective_id == perspective_id
    ).first()
    
    snapshot2 = db.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.id == snapshot_id2,
        PerspectiveSnapshot.perspective_id == perspective_id
    ).first()
    
    if not snapshot1:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id1} not found")
    if not snapshot2:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id2} not found")
    
    mem1 = snapshot1.membership or {}
    mem2 = snapshot2.membership or {}
    
    servers1 = {s["server_id"]: s for s in mem1.get("servers", [])}
    servers2 = {s["server_id"]: s for s in mem2.get("servers", [])}
    
    all_servers = set(servers1.keys()) | set(servers2.keys())
    differences = []
    
    for server_id in all_servers:
        s1 = servers1.get(server_id)
        s2 = servers2.get(server_id)
        old_tier = s1.get("tier") if s1 else None
        new_tier = s2.get("tier") if s2 else None
        
        if s1 and not s2:
            change_type = "removed"
        elif not s1 and s2:
            change_type = "added"
        elif old_tier != new_tier:
            change_type = "changed"
        else:
            continue
        
        differences.append(SnapshotDifference(
            server_id=server_id,
            change_type=change_type,
            old_tier=old_tier,
            new_tier=new_tier
        ))
    
    return DifferencesResponse(differences=differences)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    
    db = TestingSessionLocal()
    snap1 = PerspectiveSnapshot(
        perspective_id=1,
        membership={"servers": [
            {"server_id": "srv1", "tier": "gold"},
            {"server_id": "srv2", "tier": "silver"}
        ]},
        taken_at=datetime(2024, 1, 1)
    )
    snap2 = PerspectiveSnapshot(
        perspective_id=1,
        membership={"servers": [
            {"server_id": "srv1", "tier": "platinum"},
            {"server_id": "srv3", "tier": "bronze"}
        ]},
        taken_at=datetime(2024, 1, 15)
    )
    db.add(snap1)
    db.add(snap2)
    db.commit()
    db.refresh(snap1)
    db.refresh(snap2)
    db.close()
    
    response = client.get(f"/api/perspectives/1/compare/{snap1.id}/{snap2.id}")
    assert response.status_code == 200
    data = response.json()
    assert "differences" in data
    assert isinstance(data["differences"], list)
    
    diffs = data["differences"]
    assert len(diffs) == 3
    diff_map = {d["server_id"]: d for d in diffs}
    assert diff_map["srv1"]["change_type"] == "changed"
    assert diff_map["srv1"]["old_tier"] == "gold"
    assert diff_map["srv1"]["new_tier"] == "platinum"
    assert diff_map["srv2"]["change_type"] == "removed"
    assert diff_map["srv3"]["change_type"] == "added"
    
    print("PASS")