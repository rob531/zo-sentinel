from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from . import logic as _logic
from .logic import *  # noqa: F401,F403


router = APIRouter(prefix="/api", tags=["vuln-advisory-age-rollup"])


class AdvisoryAgeRollupResponse(BaseModel):
    days: int
    total_advisories: int
    by_severity: Dict[str, int]
    by_ecosystem: Dict[str, int]
    by_feed: Dict[str, int]
    stale_sync_count: int
    unlinked_count: int
    low_confidence_link_count: int
    linked_servers_by_severity: Dict[str, int]


def _fallback_rollup(session: Session, days: int) -> Dict[str, Any]:
    advisories = session.query(VulnAdvisory).all()
    links = session.query(VulnLink).all()

    by_severity: Dict[str, int] = {}
    by_ecosystem: Dict[str, int] = {}
    by_feed: Dict[str, int] = {}
    linked_servers_by_severity: Dict[str, set[str]] = {}
    linked_advisory_ids = set()
    low_confidence_link_count = 0
    stale_sync_count = 0

    for advisory in advisories:
        severity = advisory.severity or "unknown"
        ecosystem = advisory.ecosystem or "unknown"
        feed = advisory.feed or "unknown"

        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_ecosystem[ecosystem] = by_ecosystem.get(ecosystem, 0) + 1
        by_feed[feed] = by_feed.get(feed, 0) + 1

        published_at = advisory.published_at
        fetched_at = advisory.fetched_at
        if published_at is not None and fetched_at is not None:
            if fetched_at - published_at > timedelta(days=days):
                stale_sync_count += 1

    for link in links:
        linked_advisory_ids.add(link.advisory_id)
        if link.match_confidence is not None and link.match_confidence < 0.5:
            low_confidence_link_count += 1

        advisory = session.get(VulnAdvisory, link.advisory_id)
        if advisory is not None:
            severity = advisory.severity or "unknown"
            linked_servers_by_severity.setdefault(severity, set()).add(
                str(link.server_id)
            )

    unlinked_count = sum(
        1 for advisory in advisories if advisory.id not in linked_advisory_ids
    )

    return {
        "days": days,
        "total_advisories": len(advisories),
        "by_severity": by_severity,
        "by_ecosystem": by_ecosystem,
        "by_feed": by_feed,
        "stale_sync_count": stale_sync_count,
        "unlinked_count": unlinked_count,
        "low_confidence_link_count": low_confidence_link_count,
        "linked_servers_by_severity": {
            severity: len(server_ids)
            for severity, server_ids in linked_servers_by_severity.items()
        },
    }


def _logic_function() -> Optional[Any]:
    preferred = (
        "get_advisory_age_rollup",
        "compute_advisory_age_rollup",
        "calculate_advisory_age_rollup",
        "get_vuln_advisory_age_rollup",
        "advisory_age_rollup",
        "get_age_rollup",
        "compute_age_rollup",
    )
    for name in preferred:
        candidate = getattr(_logic, name, None)
        if inspect.isfunction(candidate):
            return candidate

    candidates = []
    for name, candidate in vars(_logic).items():
        if not inspect.isfunction(candidate):
            continue
        if candidate.__module__ != _logic.__name__:
            continue
        lowered = name.lower()
        if "response" in lowered or "router" in lowered:
            continue
        score = 0
        if "advisory" in lowered:
            score += 2
        if "rollup" in lowered:
            score += 2
        if "age" in lowered:
            score += 1
        if score:
            candidates.append((score, name, candidate))

    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][2]
    return None


def _invoke_logic(session: Session, days: int) -> Dict[str, Any]:
    function = _logic_function()
    if function is None:
        return _fallback_rollup(session, days)

    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    kwargs: Dict[str, Any] = {}
    positional: List[Any] = []

    for parameter in parameters:
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        name = parameter.name.lower()
        if name == "days" or "window" in name or "age" in name:
            value = days
        elif name in {"db", "session", "database"} or parameter.annotation is Session:
            value = session
        elif parameter.default is not inspect.Parameter.empty:
            continue
        else:
            positional.append(session)
            continue
        kwargs[parameter.name] = value

    try:
        result = function(**kwargs) if kwargs else function(*positional)
    except TypeError:
        result = function(session, days)

    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("days", days)
        return payload
    if hasattr(result, "__dict__"):
        payload = dict(vars(result))
        payload.setdefault("days", days)
        return payload
    return _fallback_rollup(session, days)


@router.get(
    "/vuln/advisory-age",
    response_model=AdvisoryAgeRollupResponse,
    summary="Roll up vulnerability advisory age and linkage metrics",
)
def advisory_age_rollup(
    days: int = 30,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _invoke_logic(session, days)


get_advisory_age_rollup = advisory_age_rollup


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        for advisory_id in range(1, 6):
            published_at = now - timedelta(days=40 if advisory_id == 1 else 2)
            session.add(
                VulnAdvisory(
                    id=advisory_id,
                    feed="test-feed",
                    severity="critical" if advisory_id <= 2 else "high",
                    ecosystem="pypi",
                    package=f"package-{advisory_id}",
                    published_at=published_at,
                    fetched_at=now,
                    identities=None,
                    content_hash=f"hash-{advisory_id}",
                    source_url=f"https://example.test/{advisory_id}",
                )
            )

        for link_id in range(1, 5):
            session.add(
                VulnLink(
                    id=link_id,
                    advisory_id=link_id,
                    server_id=f"server-{link_id}",
                    match_basis="package",
                    match_confidence=0.4 if link_id == 1 else 0.9,
                    match_value=f"package-{link_id}",
                    linked_at=now,
                )
            )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestingSession()

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisory-age", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 5
        assert payload["by_severity"]["critical"] == 2
        assert payload["stale_sync_count"] == 1

    print("PASS")