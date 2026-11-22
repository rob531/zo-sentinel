{
        "perspective_id": int,
        "name": str,
        "snapshots": [
            {"taken_at": "2024-01-01T12:00:00Z", "member_count": 3},
            ...
        ],
        "member_count_delta": int,
        "events": [
            {"change_type": "tier_up", "old_tier": "low", "new_tier": "high", "count": 1},
            ...
        ],
        "total_events": int
    }