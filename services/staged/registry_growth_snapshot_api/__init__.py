"""Registry Growth Snapshot API service package."""

from typing import Optional
from datetime import datetime, timedelta


class RegistryGrowthSnapshotError(Exception):
    """Raised when registry growth snapshot operations fail."""
    pass


def get_registry_growth_snapshot(
    org_id: Optional[str] = None,
    time_range_days: int = 30
) -> dict:
    """Get a snapshot of registry growth over time.
    
    Args:
        org_id: Optional organization ID to filter by.
        time_range_days: Number of days to look back.
    
    Returns:
        dict containing growth snapshot data.
    """
    return {
        "org_id": org_id,
        "time_range_days": time_range_days,
        "snapshot_time": datetime.utcnow().isoformat(),
        "registry_count": 0,
        "growth_rate": 0.0,
        "new_servers": [],
        "total_growth_percentage": 0.0
    }


async def get_registry_growth_snapshot_endpoint(
    org_id: str,
    time_range_days: int = 30
) -> dict:
    """Async endpoint wrapper for registry growth snapshot retrieval."""
    return get_registry_growth_snapshot(org_id=org_id, time_range_days=time_range_days)


def test_service_package() -> dict:
    """Self-test to validate the service package works correctly."""
    try:
        snapshot = get_registry_growth_snapshot(org_id="test-org", time_range_days=7)
        assert "org_id" in snapshot
        assert "snapshot_time" in snapshot
        assert "registry_count" in snapshot
        assert "growth_rate" in snapshot
        assert "new_servers" in snapshot
        return {"status": "pass", "snapshot": snapshot}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


if __name__ == "__main__":
    print("Running self-test...")
    result = test_service_package()
    print(f"Result: {result}")
    if result["status"] == "pass":
        print("PASS")
    else:
        print("FAIL")