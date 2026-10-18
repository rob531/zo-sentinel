from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["perspective_tier_drift"])


class TierMembership(BaseModel):
    trusted: int = 0
    research: int = 0
    controlled: int = 0
    caution: int = 0
    high_risk: int = 0
    insufficient: int = 0


class TransitionEntry(BaseModel):
    date: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int


class TierDriftResponse(BaseModel):
    perspective_id: str
    days: int
    transitions: list[TransitionEntry]
    current_membership: TierMembership


@router.get("/perspectives/{perspective_id}/tier-drift", response_model=TierDriftResponse)
def get_tier_drift(
    perspective_id: str,
    days: int = 30,
    session: Session = Depends(get_session)
):
    result = get_tier_drift_logic(session, perspective_id, days)
    if result is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return result


def get_tier_drift_logic(session: Session, perspective_id: str, days: int) -> Optional[dict]:
    start_date = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    
    query = text("""
        SELECT 
            DATE(pe.created_at) as transition_date,
            pe.old_tier,
            pe.new_tier,
            COUNT(*) as count
        FROM perspective_events pe
        WHERE pe.perspective_id = :perspective_id
          AND pe.change_type = 'tier_change'
          AND DATE(pe.created_at) >= :start_date
        GROUP BY DATE(pe.created_at), pe.old_tier, pe.new_tier
        ORDER BY transition_date, pe.old_tier, pe.new_tier
    """)
    
    result = session.execute(query, {"perspective_id": perspective_id, "start_date": start_date})
    rows = result.fetchall()
    
    transitions = [
        {
            "date": row.transition_date.isoformat() if hasattr(row.transition_date, 'isoformat') else str(row.transition_date),
            "old_tier": row.old_tier,
            "new_tier": row.new_tier,
            "count": row.count
        }
        for row in rows
    ]
    
    snapshot_query = text("""
        SELECT ps.membership
        FROM perspective_snapshots ps
        WHERE ps.perspective_id = :perspective_id
        ORDER BY ps.taken_at DESC
        LIMIT 1
    """)
    
    snapshot_result = session.execute(snapshot_query, {"perspective_id": perspective_id})
    snapshot_row = snapshot_result.fetchone()
    
    if snapshot_row and snapshot_row.membership:
        membership = snapshot_row.membership
        current_membership = {
            "trusted": membership.get("trusted", 0),
            "research": membership.get("research", 0),
            "controlled": membership.get("controlled", 0),
            "caution": membership.get("caution", 0),
            "high_risk": membership.get("high_risk", 0),
            "insufficient": membership.get("insufficient", 0)
        }
    else:
        current_membership = {
            "trusted": 0,
            "research": 0,
            "controlled": 0,
            "caution": 0,
            "high_risk": 0,
            "insufficient": 0
        }
    
    return {
        "perspective_id": perspective_id,
        "days": days,
        "transitions": transitions,
        "current_membership": current_membership
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')
    from logic import get_tier_drift_logic
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id TEXT NOT NULL,
                server_id TEXT,
                change_type TEXT,
                old_tier TEXT,
                new_tier TEXT,
                created_at TIMESTAMP,
                seen BOOLEAN
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                verdict TEXT,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                verdict_reasoning TEXT,
                meta TEXT,
                scan_count INTEGER,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE perspective_snapshots (
                id INTEGER PRIMARY KEY,
                perspective_id TEXT NOT NULL,
                membership TEXT,
                taken_at TIMESTAMP
            )
        """))
        conn.commit()
    
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    
    day1 = datetime.utcnow() - timedelta(days=1)
    day2 = datetime.utcnow() - timedelta(days=2)
    
    session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, name, url, risk_tier) VALUES
        ('srv-1', 'Server One', 'http://srv1.local', 'trusted'),
        ('srv-2', 'Server Two', 'http://srv2.local', 'research'),
        ('srv-3', 'Server Three', 'http://srv3.local', 'controlled')
    """))
    
    session.execute(text("""
        INSERT INTO perspective_events (perspective_id, server_id, change_type, old_tier, new_tier, created_at, seen) VALUES
        ('test-persp', 'srv-1', 'tier_change', 'research', 'trusted', :d1, 1),
        ('test-persp', 'srv-2', 'tier_change', 'controlled', 'research', :d1, 1),
        ('test-persp', 'srv-1', 'tier_change', 'trusted', 'research', :d2, 1),
        ('test-persp', 'srv-3', 'tier_change', 'caution', 'controlled', :d2, 1),
        ('test-persp', 'srv-2', 'tier_change', 'research', 'trusted', :d2, 1)
    """), {"d1": day1, "d2": day2})
    
    session.execute(text("""
        INSERT INTO perspective_snapshots (perspective_id, membership, taken_at) VALUES
        ('test-persp', '{"trusted": 2, "research": 1, "controlled": 0, "caution": 0, "high_risk": 0, "insufficient": 0}', :now)
    """), {"now": datetime.utcnow()})
    
    session.commit()
    
    result = get_tier_drift_logic(session, "test-persp", 30)
    
    assert result is not None, "Result should not be None"
    assert result["perspective_id"] == "test-persp"
    assert result["days"] == 30
    assert len(result["transitions"]) > 0, "Transitions should be non-empty"
    
    transition_pairs = [(t["old_tier"], t["new_tier"]) for t in result["transitions"]]
    assert ("research", "trusted") in transition_pairs, "Expected transition research->trusted not found"
    
    assert result["current_membership"]["trusted"] == 2
    assert result["current_membership"]["research"] == 1
    
    print("PASS")