"""Data and computation layer for verdict conflict verification."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


_DAYS_DEFAULT = 30
_LIMIT_DEFAULT = 200
_LIMIT_MAX = 1000

_TIER_THRESHOLDS: Tuple[Tuple[float, str], ...] = (
    (0.40, "low"),
    (0.70, "medium"),
    (float("inf"), "high"),
)

_TIER_ALIASES = {
    "low": "low",
    "tier1": "low",
    "tier_1": "low",
    "green": "low",
    "safe": "low",
    "medium": "medium",
    "moderate": "medium",
    "tier2": "medium",
    "tier_2": "medium",
    "amber": "medium",
    "review": "medium",
    "high": "high",
    "severe": "high",
    "tier3": "high",
    "tier_3": "high",
    "red": "high",
    "critical": "high",
}

_VERDICT_ALIASES = {
    "low": ("safe", "allow", "allowed", "trusted", "approved", "low"),
    "medium": (
        "review",
        "warning",
        "warn",
        "caution",
        "moderate",
        "conditional",
        "medium",
    ),
    "high": (
        "unsafe",
        "block",
        "blocked",
        "deny",
        "denied",
        "malicious",
        "critical",
        "high",
    ),
}


class ConflictRows(list):
    """List of conflict records with coverage metadata attached."""

    def __init__(self, rows: Iterable[Dict[str, Any]], coverage: Dict[str, int]):
        super().__init__(rows)
        self.coverage = coverage

    def as_report(self) -> Dict[str, Any]:
        return {"conflicts": list(self), "coverage": dict(self.coverage)}


def _normalized(value: Optional[str]) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _tier_key(value: Optional[str]) -> Optional[str]:
    normalized = _normalized(value)
    if not normalized:
        return None
    if normalized in _TIER_ALIASES:
        return _TIER_ALIASES[normalized]
    compact = normalized.replace("_", "")
    if compact in {"tier1", "tier2", "tier3"}:
        return {"tier1": "low", "tier2": "medium", "tier3": "high"}[compact]
    return normalized


def _ordered_tiers(
    tier_counts: Counter,
) -> List[Tuple[str, str]]:
    candidates: List[Tuple[int, int, str, str]] = []
    for exact, count in tier_counts.items():
        key = _tier_key(exact)
        if not key:
            continue
        standard_rank = {"low": 0, "medium": 1, "high": 2}.get(key)
        rank = standard_rank if standard_rank is not None else 3
        candidates.append((rank, -int(count), exact, key))

    candidates.sort()
    ordered: List[Tuple[str, str]] = []
    seen_keys = set()
    for _, _, exact, key in candidates:
        if key not in seen_keys:
            ordered.append((exact, key))
            seen_keys.add(key)
    return ordered


def _derive_tier(
    p_top: float,
    ordered_tiers: Sequence[Tuple[str, str]],
) -> Optional[str]:
    if not ordered_tiers:
        return None

    threshold_tier = "high"
    for boundary, tier_key in _TIER_THRESHOLDS:
        if p_top < boundary:
            threshold_tier = tier_key
            break

    for exact, key in ordered_tiers:
        if key == threshold_tier:
            return exact

    standard_rank = {"low": 0, "medium": 1, "high": 2}.get(threshold_tier, 1)
    target_rank = round(standard_rank * (len(ordered_tiers) - 1) / 2)
    target_rank = max(0, min(target_rank, len(ordered_tiers) - 1))
    return ordered_tiers[target_rank][0]


def _canonical_verdict(
    tier: Optional[str],
    registry_rows: Sequence[McpServerRegistry],
    observed_verdicts: set[str],
) -> Optional[str]:
    tier_key = _tier_key(tier)
    if not tier_key:
        return None

    candidates = _VERDICT_ALIASES.get(tier_key, ())
    matching = sorted(candidate for candidate in candidates if candidate in observed_verdicts)

    tier_verdicts = [
        row.verdict
        for row in registry_rows
        if row.verdict and _tier_key(row.risk_tier) == tier_key
    ]
    tier_counts = Counter(tier_verdicts)

    if matching:
        matching.sort(key=lambda verdict: (-tier_counts.get(verdict, 0), verdict))
        return matching[0]

    if tier_verdicts:
        return tier_counts.most_common(1)[0][0]
    return None


def _coverage(
    total_servers: int,
    scored_servers: int,
    missing_axis_servers: int,
    stale_axis_servers: int,
    missing_p_top_servers: int,
) -> Dict[str, int]:
    return {
        "total_servers": total_servers,
        "registry_servers": total_servers,
        "scored_servers": scored_servers,
        "missing_axis": missing_axis_servers,
        "missing_axis_servers": missing_axis_servers,
        "missing_axis_count": missing_axis_servers,
        "stale_axis_servers": stale_axis_servers,
        "missing_p_top_servers": missing_p_top_servers,
    }


def get_verdict_conflicts(
    db: Session,
    days: int = _DAYS_DEFAULT,
    limit: int = _LIMIT_DEFAULT,
) -> ConflictRows:
    """Return registry records that disagree with the latest overall-risk tier."""
    days = max(0, int(days))
    limit = max(1, min(int(limit), _LIMIT_MAX))

    ranked_scores = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.p_top.label("p_top"),
            McpLlmAxisScore.scored_at.label("scored_at"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )

    rows = db.execute(
        select(McpServerRegistry, ranked_scores.c.p_top, ranked_scores.c.scored_at)
        .outerjoin(
            ranked_scores,
            and_(
                McpServerRegistry.server_id == ranked_scores.c.server_id,
                ranked_scores.c.row_number == 1,
            ),
        )
    ).all()

    registry_rows = [row[0] for row in rows]
    tier_counts = Counter(
        row.risk_tier for row in registry_rows if row.risk_tier and row.risk_tier.strip()
    )
    ordered_tiers = _ordered_tiers(tier_counts)
    observed_verdicts = {
        _normalized(row.verdict) for row in registry_rows if row.verdict and row.verdict.strip()
    }

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conflicts: List[Dict[str, Any]] = []
    scored_servers = 0
    missing_axis_servers = 0
    stale_axis_servers = 0
    missing_p_top_servers = 0

    for registry, p_top, scored_at in rows:
        has_score = scored_at is not None or p_top is not None
        if not has_score:
            missing_axis_servers += 1
            continue

        if p_top is None:
            missing_p_top_servers += 1
            continue

        if scored_at is not None:
            comparable_scored_at = scored_at
            if comparable_scored_at.tzinfo is None:
                comparable_scored_at = comparable_scored_at.replace(tzinfo=timezone.utc)
            if comparable_scored_at < cutoff:
                stale_axis_servers += 1
                continue

        scored_servers += 1
        derived_tier = _derive_tier(float(p_top), ordered_tiers)
        if derived_tier is None:
            continue

        expected_verdict = _canonical_verdict(
            derived_tier,
            registry_rows,
            observed_verdicts,
        )
        tier_disagrees = registry.risk_tier != derived_tier
        verdict_disagrees = (
            expected_verdict is not None
            and _normalized(registry.verdict) != _normalized(expected_verdict)
        )

        if not tier_disagrees and not verdict_disagrees:
            continue
        if tier_disagrees and verdict_disagrees:
            gap = "tier_mismatch"
        elif tier_disagrees:
            gap = "tier_only"
        else:
            gap = "verdict_only"

        conflicts.append(
            {
                "server_id": str(registry.server_id),
                "name": registry.name,
                "registry_verdict": registry.verdict,
                "registry_risk_tier": registry.risk_tier,
                "derived_tier": derived_tier,
                "gap": gap,
            }
        )

    conflicts.sort(key=lambda item: (item["gap"], item["server_id"]))
    coverage = _coverage(
        total_servers=len(registry_rows),
        scored_servers=scored_servers,
        missing_axis_servers=missing_axis_servers,
        stale_axis_servers=stale_axis_servers,
        missing_p_top_servers=missing_p_top_servers,
    )
    return ConflictRows(conflicts[:limit], coverage)


def list_verdict_conflicts(
    db: Session,
    days: int = _DAYS_DEFAULT,
    limit: int = _LIMIT_DEFAULT,
) -> ConflictRows:
    """Compatibility alias for callers that use list-style naming."""
    return get_verdict_conflicts(db, days=days, limit=limit)


def get_conflicts(
    db: Session,
    days: int = _DAYS_DEFAULT,
    limit: int = _LIMIT_DEFAULT,
) -> ConflictRows:
    """Compatibility alias for callers that use the concise route name."""
    return get_verdict_conflicts(db, days=days, limit=limit)


def get_verdict_conflict_report(
    db: Session,
    days: int = _DAYS_DEFAULT,
    limit: int = _LIMIT_DEFAULT,
) -> Dict[str, Any]:
    """Return conflicts and coverage as a report dictionary."""
    result = get_verdict_conflicts(db, days=days, limit=limit)
    return result.as_report()


if __name__ == "__main__":
    from datetime import datetime as _datetime

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(bind=test_engine)
    McpLlmAxisScore.__table__.create(bind=test_engine)

    now = _datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="matching",
                    name="Matching",
                    verdict="safe",
                    risk_tier="low",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="tier-mismatch",
                    name="Tier mismatch",
                    verdict="unsafe",
                    risk_tier="high",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="missing-axis",
                    name="Missing axis",
                    verdict="safe",
                    risk_tier="low",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="verdict-only",
                    name="Verdict only",
                    verdict="unsafe",
                    risk_tier="low",
                    last_assessed=now,
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="matching",
                    axis_name="overall_risk",
                    model_version="test",
                    p_top=0.10,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="tier-mismatch",
                    axis_name="overall_risk",
                    model_version="test",
                    p_top=0.10,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="verdict-only",
                    axis_name="overall_risk",
                    model_version="test",
                    p_top=0.10,
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/verdict/conflicts")
    def verdict_conflicts(
        days: int = 30,
        limit: int = 200,
        db: Session = Depends(get_session),
    ):
        result = get_verdict_conflicts(db, days=days, limit=limit)
        return {
            "days": days,
            "count": len(result),
            "conflicts": list(result),
            "coverage": result.coverage,
        }

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/verdict/conflicts?days=30&limit=200")
    payload = response.json()

    assert response.status_code == 200
    assert payload["count"] >= 2
    mismatch = next(
        item
        for item in payload["conflicts"]
        if item["server_id"] == "tier-mismatch"
    )
    assert mismatch["gap"] == "tier_mismatch"
    assert all(
        item["server_id"] != "missing-axis" for item in payload["conflicts"]
    )
    assert payload["coverage"]["missing_axis"] >= 1
    print("PASS")