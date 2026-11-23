"""logic.py -- advisory identity-map data and computation layer."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api/advisories", tags=["advisories"])


class IdentityKind(BaseModel):
    kind: str
    count: int
    examples: List[str]


class AdvisoryIdentityMapFeed(BaseModel):
    feed: str
    total_advisories: int
    identity_kinds: List[IdentityKind]


class AdvisoryIdentityMapResponse(BaseModel):
    feeds: List[AdvisoryIdentityMapFeed]


def _identity_values(value: Any) -> List[str]:
    """Return identity strings from the JSON value stored in identities."""
    if value is None:
        return []

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = value
        if isinstance(decoded, list):
            value = decoded
        else:
            value = [decoded]
    elif isinstance(value, (tuple, set)):
        value = list(value)
    elif not isinstance(value, list):
        value = [value]

    values: List[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                values.append(text)
        elif isinstance(item, dict):
            for key in ("value", "identity", "id", "name"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    values.append(candidate.strip())
                    break
    return values


def _identity_kind(identity: str) -> str:
    upper = identity.upper()
    if upper.startswith("CVE-"):
        return "CVE"
    if upper.startswith("GHSA-"):
        return "GHSA"
    if upper.startswith("OSV-"):
        return "OSV"
    return "other"


def get_identity_map(
    db: Session,
    feed: Optional[str] = None,
    ecosystem: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Build the distinct identity vocabulary emitted by each advisory feed."""
    if limit <= 0:
        return {"feeds": []}

    statement = select(VulnAdvisory.feed, VulnAdvisory.identities)
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)
    if ecosystem is not None:
        statement = statement.where(VulnAdvisory.ecosystem == ecosystem)

    rows = db.execute(statement).all()

    totals: Dict[str, int] = defaultdict(int)
    identities_by_feed: Dict[str, set[str]] = defaultdict(set)
    for row_feed, identities in rows:
        feed_name = row_feed or "unknown"
        totals[feed_name] += 1
        identities_by_feed[feed_name].update(_identity_values(identities))

    output: List[Dict[str, Any]] = []
    for feed_name in sorted(identities_by_feed):
        kind_values: Dict[str, set[str]] = defaultdict(set)
        for identity in identities_by_feed[feed_name]:
            kind_values[_identity_kind(identity)].add(identity)

        identity_kinds = []
        for kind in ("CVE", "GHSA", "OSV", "other"):
            if kind not in kind_values:
                continue
            values = sorted(kind_values[kind])
            identity_kinds.append(
                {
                    "kind": kind,
                    "count": len(values),
                    "examples": values[:10],
                }
            )

        output.append(
            {
                "feed": feed_name,
                "total_advisories": totals[feed_name],
                "identity_kinds": identity_kinds,
            }
        )
        if len(output) >= limit:
            break

    return {"feeds": output}


@router.get(
    "/identity-map",
    response_model=AdvisoryIdentityMapResponse,
    summary="List identity vocabularies emitted by advisory feeds",
)
def advisory_identity_map(
    feed: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_identity_map(db, feed=feed, ecosystem=ecosystem, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    SessionLocal = sessionmaker(bind=test_engine)
    test_session = SessionLocal()

    test_session.add_all(
        [
            VulnAdvisory(
                id="identity-map-1",
                feed="feed-a",
                source_url="https://example.invalid/a",
                ecosystem="npm",
                identities=["CVE-2024-0001", "GHSA-abcd-1234-efgh"],
            ),
            VulnAdvisory(
                id="identity-map-2",
                feed="feed-b",
                source_url="https://example.invalid/b",
                ecosystem="pypi",
                identities=["OSV-2024-0002"],
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/advisories/identity-map")
    def test_identity_map(
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_identity_map(db)

    test_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(test_app).get("/api/advisories/identity-map")
    assert response.status_code == 200
    body = response.json()
    assert len(body["feeds"]) == 2
    assert len(body["feeds"][0]["identity_kinds"]) == 2
    assert len(body["feeds"][1]["identity_kinds"]) == 1
    assert body["feeds"][1]["identity_kinds"][0]["kind"] == "OSV"
    print("PASS")