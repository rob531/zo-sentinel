"""perspective_tier_rollup_report.py -- read-only rollup of perspective tier changes.

Pure data reduction over `perspective_events` rows (perspective_id, server_id,
change_type, old_tier, new_tier, created_at) into per-perspective transition
statistics for the dashboard surfaces. No DB writes, no network calls.

Tier order (low -> high severity):
    TRUSTED_GENERAL < TRUSTED_RESEARCH < ENTERPRISE_CONTROLLED
    < CAUTION_LIMITED < HIGH_RISK_ISOLATED < KNOWN_THREAT

A transition to a strictly higher severity tier is an "upgrade", to a strictly
lower one a "downgrade"; everything else (same tier, unknown tiers, NULL
tiers) is "unchanged". Unknown tier strings form their own bucket and are
never raised as errors.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import PerspectiveEvent

TIER_ORDER = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
)

NULL_TIER = "NULL"

_TIER_RANK = {tier: idx for idx, tier in enumerate(TIER_ORDER)}

_BUCKET_KEY = {"upgrade": "upgrades", "downgrade": "downgrades",
               "unchanged": "unchanged"}


def _tier_key(tier: Optional[str]) -> str:
    """Canonical bucket label for a tier value (NULL/blank -> the NULL bucket)."""
    if tier is None:
        return NULL_TIER
    text = str(tier).strip()
    return text.upper() if text else NULL_TIER


def _rank(tier: Optional[str]) -> Optional[int]:
    """Severity rank of a tier, or None when it is NULL/unknown."""
    if tier is None:
        return None
    text = str(tier).strip().upper()
    if not text:
        return None
    return _TIER_RANK.get(text)


def classify_transition(old_tier: Optional[str], new_tier: Optional[str]) -> str:
    """'upgrade' | 'downgrade' | 'unchanged' for one tier transition."""
    old_rank = _rank(old_tier)
    new_rank = _rank(new_tier)
    if old_rank is None or new_rank is None:
        return "unchanged"
    if new_rank > old_rank:
        return "upgrade"
    if new_rank < old_rank:
        return "downgrade"
    return "unchanged"


def _top_new_tier(new_tiers: List[Optional[str]]) -> Optional[str]:
    """Most frequent non-NULL new tier; ties break toward the higher severity."""
    counts = Counter(_tier_key(t) for t in new_tiers if _tier_key(t) != NULL_TIER)
    if not counts:
        return None

    def _sort_key(item):
        tier, count = item
        rank = _TIER_RANK.get(tier)
        return (-count, -(rank if rank is not None else -1), tier)

    return sorted(counts.items(), key=_sort_key)[0][0]


def rollup_perspective_transitions(events: List[dict]) -> dict:
    """Roll perspective event dicts into per-perspective transition statistics."""
    rows = list(events or [])
    per_perspective: Dict[str, Dict[str, Any]] = {}
    matrix: Counter = Counter()
    all_servers: set = set()
    totals = {"upgrades": 0, "downgrades": 0, "unchanged": 0}

    for row in rows:
        if not isinstance(row, dict):
            continue
        perspective_id = str(row.get("perspective_id") or "")
        server_id = str(row.get("server_id") or "")
        old_tier = row.get("old_tier")
        new_tier = row.get("new_tier")

        bucket = per_perspective.setdefault(perspective_id, {
            "perspective_id": perspective_id,
            "events": 0,
            "servers": set(),
            "upgrades": 0,
            "downgrades": 0,
            "unchanged": 0,
            "new_tiers": [],
        })
        kind = classify_transition(old_tier, new_tier)
        bucket["events"] += 1
        bucket["servers"].add(server_id)
        bucket[_BUCKET_KEY[kind]] += 1
        bucket["new_tiers"].append(new_tier)
        totals[_BUCKET_KEY[kind]] += 1
        all_servers.add(server_id)
        matrix[f"{_tier_key(old_tier)}->{_tier_key(new_tier)}"] += 1

    by_perspective = []
    for perspective_id in sorted(per_perspective):
        bucket = per_perspective[perspective_id]
        by_perspective.append({
            "perspective_id": bucket["perspective_id"],
            "events": bucket["events"],
            "servers": len(bucket["servers"]),
            "upgrades": bucket["upgrades"],
            "downgrades": bucket["downgrades"],
            "unchanged": bucket["unchanged"],
            "top_new_tier": _top_new_tier(bucket["new_tiers"]),
        })

    return {
        "perspectives": len(per_perspective),
        "events": len(rows),
        "servers": len(all_servers),
        "upgrades": totals["upgrades"],
        "downgrades": totals["downgrades"],
        "unchanged": totals["unchanged"],
        "by_perspective": by_perspective,
        "transition_matrix": dict(matrix),
    }


def summarize(events: List[dict]) -> str:
    """Short multi-line human-readable digest of the same numbers."""
    roll = rollup_perspective_transitions(events)
    lines = [
        f"perspectives: {roll['perspectives']}",
        f"events: {roll['events']}",
        f"servers: {roll['servers']}",
        f"upgrades: {roll['upgrades']}  downgrades: {roll['downgrades']}  "
        f"unchanged: {roll['unchanged']}",
    ]
    for row in roll["by_perspective"]:
        lines.append(
            f"- {row['perspective_id']}: {row['events']} events, {row['servers']} servers, "
            f"up {row['upgrades']} / down {row['downgrades']} / same {row['unchanged']}, "
            f"top_new_tier={row['top_new_tier']}"
        )
    if roll["transition_matrix"]:
        top = sorted(roll["transition_matrix"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        lines.append("transitions: " + ", ".join(f"{k}={v}" for k, v in top))
    else:
        lines.append("transitions: none")
    return "\n".join(lines)


def load_perspective_events(db, perspective_id: Optional[str] = None,
                            limit: int = 1000) -> List[dict]:
    """Read perspective_events rows through the app data layer -> event dicts."""
    query = db.query(PerspectiveEvent)
    if perspective_id:
        query = query.filter(PerspectiveEvent.perspective_id == perspective_id)
    rows = query.order_by(PerspectiveEvent.created_at.desc()) \
               .limit(max(1, min(int(limit or 1000), 10000))).all()
    events = []
    for row in rows:
        created = row.created_at
        events.append({
            "perspective_id": row.perspective_id,
            "server_id": row.server_id,
            "change_type": row.change_type,
            "old_tier": row.old_tier,
            "new_tier": row.new_tier,
            "created_at": created.isoformat() if created is not None else None,
        })
    return events


def fetch_perspective_events(perspective_id: Optional[str] = None,
                             limit: int = 1000) -> List[dict]:
    """Open the app session (get_session) and roll the rows out of it."""
    for db in get_session():
        return load_perspective_events(db, perspective_id=perspective_id, limit=limit)
    return []


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(PerspectiveEvent(id=1, perspective_id="p1", server_id="srv1",
                            change_type="tier_changed", old_tier="CAUTION_LIMITED",
                            new_tier="HIGH_RISK_ISOLATED"))
    s.add(PerspectiveEvent(id=2, perspective_id="p1", server_id="srv2",
                            change_type="tier_changed", old_tier="HIGH_RISK_ISOLATED",
                            new_tier="CAUTION_LIMITED"))
    s.add(PerspectiveEvent(id=3, perspective_id="p1", server_id="srv3",
                            change_type="tier_changed", old_tier="CAUTION_LIMITED",
                            new_tier="CAUTION_LIMITED"))
    s.commit()
    s.close()

    mixed = [
        {"perspective_id": "p1", "server_id": "srv1", "change_type": "tier_changed",
         "old_tier": "CAUTION_LIMITED", "new_tier": "HIGH_RISK_ISOLATED",
         "created_at": "2026-11-01T00:00:00+00:00"},
        {"perspective_id": "p1", "server_id": "srv2", "change_type": "tier_changed",
         "old_tier": "HIGH_RISK_ISOLATED", "new_tier": "CAUTION_LIMITED",
         "created_at": "2026-11-01T01:00:00+00:00"},
        {"perspective_id": "p1", "server_id": "srv3", "change_type": "tier_changed",
         "old_tier": "CAUTION_LIMITED", "new_tier": "CAUTION_LIMITED",
         "created_at": "2026-11-01T02:00:00+00:00"},
    ]
    roll = rollup_perspective_transitions(mixed)
    assert roll["perspectives"] == 1, roll
    assert roll["events"] == 3, roll
    assert roll["servers"] == 3, roll
    assert roll["upgrades"] == 1, roll
    assert roll["downgrades"] == 1, roll
    assert roll["unchanged"] == 1, roll
    assert roll["transition_matrix"]["CAUTION_LIMITED->HIGH_RISK_ISOLATED"] == 1, roll
    assert roll["by_perspective"][0]["top_new_tier"] == "CAUTION_LIMITED", roll

    null_event = [{"perspective_id": "p2", "server_id": "srv4", "change_type": "entered",
                   "old_tier": None, "new_tier": None,
                   "created_at": "2026-11-02T00:00:00+00:00"}]
    null_roll = rollup_perspective_transitions(null_event)
    assert null_roll["events"] == 1 and null_roll["unchanged"] == 1, null_roll
    assert null_roll["transition_matrix"]["NULL->NULL"] == 1, null_roll
    assert null_roll["by_perspective"][0]["top_new_tier"] is None, null_roll

    unknown_roll = rollup_perspective_transitions([
        {"perspective_id": "p3", "server_id": "srv5", "change_type": "tier_changed",
         "old_tier": "MYSTERY_TIER", "new_tier": "ANOTHER_TIER",
         "created_at": "2026-11-03T00:00:00+00:00"}])
    assert unknown_roll["unchanged"] == 1, unknown_roll
    assert unknown_roll["transition_matrix"]["MYSTERY_TIER->ANOTHER_TIER"] == 1, unknown_roll

    empty_roll = rollup_perspective_transitions([])
    assert empty_roll["perspectives"] == 0 and empty_roll["events"] == 0, empty_roll

    digest = summarize(mixed)
    assert isinstance(digest, str) and digest and "perspectives" in digest, digest
    assert "\n" in digest, digest

    db = TS()
    loaded = load_perspective_events(db)
    assert len(loaded) == 3, loaded
    assert rollup_perspective_transitions(loaded)["events"] == 3, loaded
    scoped = load_perspective_events(db, perspective_id="p1")
    assert len(scoped) == 3, scoped
    db.close()

    print("PASS")
