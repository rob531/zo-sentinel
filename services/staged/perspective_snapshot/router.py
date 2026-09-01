from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime
import sqlite3
import json

router = APIRouter(prefix="/api", tags=["perspective_snapshot"])

class PerspectiveSnapshotResponse(BaseModel):
    perspective_id: int
    name: str
    description: Optional[str]
    org_id: int
    facet_filters: Dict[str, Any]
    snapshot: Dict[str, Any]

def get_db_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn

def init_test_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE perspectives (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            org_id INTEGER NOT NULL,
            facet_filters TEXT
        );
        CREATE TABLE perspective_snapshots (
            id INTEGER PRIMARY KEY,
            perspective_id INTEGER NOT NULL,
            taken_at TEXT NOT NULL,
            membership TEXT
        );
        INSERT INTO perspectives (id, name, description, org_id, facet_filters) VALUES
            (1, 'Perspective One', 'First perspective', 100, '{"regions": ["us"]}'),
            (2, 'Perspective Two', 'Second perspective', 101, '{"regions": ["eu"]}');
        INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership) VALUES
            (1, 1, '2024-01-15T10:00:00', '{"users": ["alice", "bob"]}'),
            (2, 2, '2024-01-16T11:00:00', '{"users": ["charlie", "diana"]}');
    """)
    conn.commit()

async def get_perspective_snapshot(perspective_id: int, conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT p.id, p.name, p.description, p.org_id, p.facet_filters,
               ps.id as snapshot_id, ps.taken_at, ps.membership
        FROM perspectives p
        LEFT JOIN perspective_snapshots ps ON p.id = ps.perspective_id
        WHERE p.id = ?
        ORDER BY ps.taken_at DESC
        LIMIT 1
        """,
        (perspective_id,)
    )
    row = cur.fetchone()
    if not row:
        return None
    perspective_data = dict(row)
    if perspective_data.get('snapshot_id') is None:
        return {"perspective": perspective_data, "snapshot": None}
    return {
        "perspective": perspective_data,
        "snapshot": {
            "id": perspective_data['snapshot_id'],
            "taken_at": perspective_data['taken_at'],
            "membership": json.loads(perspective_data['membership']) if perspective_data['membership'] else {}
        }
    }

@router.get("/perspectives/{perspective_id}/snapshot", response_model=PerspectiveSnapshotResponse)
async def get_snapshot(perspective_id: int) -> PerspectiveSnapshotResponse:
    conn = get_db_connection()
    try:
        result = await get_perspective_snapshot(perspective_id, conn)
        if not result or result.get("perspective") is None:
            raise HTTPException(status_code=404, detail="Perspective not found")
        if result.get("snapshot") is None:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        persp = result["perspective"]
        snap = result["snapshot"]
        return PerspectiveSnapshotResponse(
            perspective_id=persp['id'],
            name=persp['name'],
            description=persp['description'],
            org_id=persp['org_id'],
            facet_filters=json.loads(persp['facet_filters']) if persp['facet_filters'] else {},
            snapshot=snap
        )
    finally:
        conn.close()

if __name__ == "__main__":
    import asyncio
    from unittest.mock import patch, MagicMock
    
    async def run_tests():
        conn = get_db_connection()
        init_test_db(conn)
        
        print("Test 1: Existing perspective with snapshot")
        result = await get_perspective_snapshot(1, conn)
        assert result is not None
        assert result["perspective"] is not None
        assert result["snapshot"] is not None
        assert result["snapshot"]["membership"] == {"users": ["alice", "bob"]}
        print("  PASS: Got snapshot for perspective 1")
        
        print("Test 2: Second perspective")
        result = await get_perspective_snapshot(2, conn)
        assert result is not None
        assert result["snapshot"]["membership"] == {"users": ["charlie", "diana"]}
        print("  PASS: Got snapshot for perspective 2")
        
        print("Test 3: Unknown perspective")
        result = await get_perspective_snapshot(999, conn)
        assert result is None
        print("  PASS: Got None for unknown perspective")
        
        print("Test 4: Membership is dict")
        result = await get_perspective_snapshot(1, conn)
        assert isinstance(result["snapshot"]["membership"], dict)
        print("  PASS: Membership is dict")
        
        conn.close()
        print("PASS")
    
    asyncio.run(run_tests())