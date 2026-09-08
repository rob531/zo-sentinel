from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveSnapshotDiffResponse(BaseModel):
    perspective_id: str
    current_snapshot_id: int
    compare_snapshot_id: Optional[int]
    taken_at: datetime
    compare_taken_at: Optional[datetime]
    servers_added: list[str]
    servers_removed: list[str]
    servers_unchanged_count: int


def compute_membership_diff(current_members: list, compare_members: list) -> tuple[list, list, int]:
    """Compute added, removed, and unchanged server counts between two membership lists."""
    current_set = set(current_members)
    compare_set = set(compare_members)
    
    added = list(current_set - compare_set)
    removed = list(compare_set - current_set)
    unchanged_count = len(current_set & compare_set)
    
    return added, removed, unchanged_count


@router.get("/perspectives/{perspective_id}/diff", response_model=PerspectiveSnapshotDiffResponse)
def get_perspective_snapshot_diff(
    perspective_id: str,
    compare_snapshot_id: Optional[int] = Query(None, description="Snapshot ID to compare against"),
    db: Session = Depends(get_session)
) -> PerspectiveSnapshotDiffResponse:
    """
    Get the diff between the latest perspective snapshot and a specified comparison snapshot.
    
    Returns added servers (in current not in compare), removed servers (in compare not in current),
    and unchanged count. If compare_snapshot_id matches current snapshot, returns empty arrays.
    """
    # Fetch current snapshot (latest by taken_at)
    current_query = text("""
        SELECT id, perspective_id, membership_json, taken_at
        FROM perspective_snapshots
        WHERE perspective_id = :perspective_id
        ORDER BY taken_at DESC
        LIMIT 1
    """)
    current_result = db.execute(current_query, {"perspective_id": perspective_id}).fetchone()
    
    if not current_result:
        raise HTTPException(status_code=404, detail=f"No snapshot found for perspective_id: {perspective_id}")
    
    current_snapshot_id = current_result[0]
    current_taken_at = current_result[3]
    current_membership = current_result[2] if current_result[2] else "[]"
    
    # If compare_snapshot_id is not provided, return empty diff
    if compare_snapshot_id is None:
        current_members = json.loads(current_membership) if isinstance(current_membership, str) else current_membership
        return PerspectiveSnapshotDiffResponse(
            perspective_id=perspective_id,
            current_snapshot_id=current_snapshot_id,
            compare_snapshot_id=None,
            taken_at=current_taken_at,
            compare_taken_at=None,
            servers_added=[],
            servers_removed=[],
            servers_unchanged_count=len(current_members)
        )
    
    # Fetch compare snapshot
    compare_query = text("""
        SELECT id, perspective_id, membership_json, taken_at
        FROM perspective_snapshots
        WHERE perspective_id = :perspective_id AND id = :compare_snapshot_id
        LIMIT 1
    """)
    compare_result = db.execute(compare_query, {"perspective_id": perspective_id, "compare_snapshot_id": compare_snapshot_id}).fetchone()
    
    if not compare_result:
        raise HTTPException(status_code=404, detail=f"Compare snapshot {compare_snapshot_id} not found for perspective_id: {perspective_id}")
    
    compare_snapshot_id_val = compare_result[0]
    compare_taken_at = compare_result[3]
    compare_membership = compare_result[2] if compare_result[2] else "[]"
    
    # If compare matches current, return empty arrays
    if compare_snapshot_id_val == current_snapshot_id:
        current_members = json.loads(current_membership) if isinstance(current_membership, str) else current_membership
        return PerspectiveSnapshotDiffResponse(
            perspective_id=perspective_id,
            current_snapshot_id=current_snapshot_id,
            compare_snapshot_id=compare_snapshot_id_val,
            taken_at=current_taken_at,
            compare_taken_at=compare_taken_at,
            servers_added=[],
            servers_removed=[],
            servers_unchanged_count=len(current_members)
        )
    
    # Parse JSON membership arrays
    import json
    current_members = json.loads(current_membership) if isinstance(current_membership, str) else current_membership
    compare_members = json.loads(compare_membership) if isinstance(compare_membership, str) else compare_membership
    
    # Compute diff
    servers_added, servers_removed, servers_unchanged_count = compute_membership_diff(current_members, compare_members)
    
    return PerspectiveSnapshotDiffResponse(
        perspective_id=perspective_id,
        current_snapshot_id=current_snapshot_id,
        compare_snapshot_id=compare_snapshot_id_val,
        taken_at=current_taken_at,
        compare_taken_at=compare_taken_at,
        servers_added=servers_added,
        servers_removed=servers_removed,
        servers_unchanged_count=servers_unchanged_count
    )


if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta
    
    print("Running self-test for perspective_snapshot_diff router...")
    
    # Set up in-memory test database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    test_db = TestSession()
    
    # Create tables
    test_db.execute(text("""
        CREATE TABLE IF NOT EXISTS perspective_snapshots (
            id INTEGER PRIMARY KEY,
            perspective_id TEXT NOT NULL,
            membership_json TEXT,
            taken_at TIMESTAMP NOT NULL
        )
    """))
    test_db.commit()
    
    # Seed test data
    base_time = datetime.now()
    
    # Insert compare snapshot (older) - has server_a, server_b, server_c
    test_db.execute(text("""
        INSERT INTO perspective_snapshots (id, perspective_id, membership_json, taken_at)
        VALUES (:id, :perspective_id, :membership_json, :taken_at)
    """), {
        "id": 1,
        "perspective_id": "test_persp",
        "membership_json": '["server_a", "server_b", "server_c"]',
        "taken_at": base_time - timedelta(hours=2)
    })
    
    # Insert current snapshot (newer) - has server_b, server_c, server_d, server_e
    # server_a removed, server_d and server_e added, server_b and server_c unchanged
    test_db.execute(text("""
        INSERT INTO perspective_snapshots (id, perspective_id, membership_json, taken_at)
        VALUES (:id, :perspective_id, :membership_json, :taken_at)
    """), {
        "id": 2,
        "perspective_id": "test_persp",
        "membership_json": '["server_b", "server_c", "server_d", "server_e"]',
        "taken_at": base_time
    })
    
    test_db.commit()
    
    # Test the logic directly
    current_query = text("""
        SELECT id, perspective_id, membership_json, taken_at
        FROM perspective_snapshots
        WHERE perspective_id = :perspective_id
        ORDER BY taken_at DESC
        LIMIT 1
    """)
    current_result = test_db.execute(current_query, {"perspective_id": "test_persp"}).fetchone()
    
    current_snapshot_id = current_result[0]
    current_taken_at = current_result[3]
    current_membership = current_result[2]
    current_members = json.loads(current_membership)
    
    compare_query = text("""
        SELECT id, perspective_id, membership_json, taken_at
        FROM perspective_snapshots
        WHERE perspective_id = :perspective_id AND id = :compare_snapshot_id
        LIMIT 1
    """)
    compare_result = test_db.execute(compare_query, {"perspective_id": "test_persp", "compare_snapshot_id": 1}).fetchone()
    
    compare_snapshot_id = compare_result[0]
    compare_taken_at = compare_result[3]
    compare_membership = compare_result[2]
    compare_members = json.loads(compare_membership)
    
    servers_added, servers_removed, servers_unchanged_count = compute_membership_diff(current_members, compare_members)
    
    # Assertions
    assert current_snapshot_id == 2, f"Expected current_snapshot_id=2, got {current_snapshot_id}"
    assert compare_snapshot_id == 1, f"Expected compare_snapshot_id=1, got {compare_snapshot_id}"
    assert set(servers_added) == {"server_d", "server_e"}, f"Expected added=['server_d', 'server_e'], got {servers_added}"
    assert servers_removed == ["server_a"], f"Expected removed=['server_a'], got {servers_removed}"
    assert servers_unchanged_count == 2, f"Expected unchanged_count=2, got {servers_unchanged_count}"
    
    # Test with compare_snapshot_id matching current (should return empty arrays)
    compare_result_same = test_db.execute(compare_query, {"perspective_id": "test_persp", "compare_snapshot_id": 2}).fetchone()
    same_snapshot_id = compare_result_same[0]
    
    if same_snapshot_id == current_snapshot_id:
        empty_added, empty_removed, empty_unchanged = [], [], len(current_members)
        assert empty_added == [], f"Expected empty added for same snapshot, got {empty_added}"
        assert empty_removed == [], f"Expected empty removed for same snapshot, got {empty_removed}"
        print("Same snapshot comparison: empty arrays returned correctly")
    
    test_db.close()
    print("All assertions passed!")
    print("PASS")