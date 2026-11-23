"""logic.py -- advisory alias cross-reference data and computation layer."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory


def list_alias_crossrefs(
    db: Session,
    feed: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, object]:
    """Return aliases shared by at least two distinct advisory rows."""
    if limit < 0:
        limit = 0

    statement = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.aliases,
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)

    rows = db.execute(statement).all()
    grouped: Dict[str, Dict[str, set[str]]] = {}

    for advisory_id, advisory_feed, severity, aliases in rows:
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except (TypeError, ValueError):
                aliases = []
        if not isinstance(aliases, list):
            continue

        for alias in aliases:
            if not isinstance(alias, str):
                continue
            entry = grouped.setdefault(
                alias,
                {"advisory_ids": set(), "feeds": set(), "severities": set()},
            )
            entry["advisory_ids"].add(str(advisory_id))
            if advisory_feed is not None:
                entry["feeds"].add(str(advisory_feed))
            if severity is not None:
                entry["severities"].add(str(severity))

    aliases_output: List[Dict[str, object]] = []
    for alias in sorted(grouped):
        entry = grouped[alias]
        if len(entry["advisory_ids"]) < 2:
            continue
        aliases_output.append(
            {
                "alias": alias,
                "advisory_ids": sorted(entry["advisory_ids"]),
                "feeds": sorted(entry["feeds"]),
                "severities": sorted(entry["severities"]),
            }
        )
        if len(aliases_output) >= limit:
            break

    return {"count": len(aliases_output), "aliases": aliases_output}


def get_alias_crossrefs(
    db: Session,
    feed: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, object]:
    """Compatibility wrapper for callers using the endpoint-oriented name."""
    return list_alias_crossrefs(db, feed=feed, limit=limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as db:
        db.add_all(
            [
                VulnAdvisory(
                    id="adv-1",
                    feed="osv",
                    summary="first",
                    severity="high",
                    aliases=["GHSA-xxxx"],
                    source_url="https://example.test/adv-1",
                ),
                VulnAdvisory(
                    id="adv-2",
                    feed="ghsa",
                    summary="second",
                    severity="medium",
                    aliases=["GHSA-xxxx"],
                    source_url="https://example.test/adv-2",
                ),
                VulnAdvisory(
                    id="adv-3",
                    feed="osv",
                    summary="third",
                    severity="low",
                    aliases=["CVE-yyyy"],
                    source_url="https://example.test/adv-3",
                ),
            ]
        )
        db.commit()

    def override_session():
        with SessionLocal() as db:
            yield db

    self_test_app = FastAPI()
    self_test_app.dependency_overrides[get_session] = override_session

    @self_test_app.get("/api/advisories/alias-crossref")
    def alias_crossref_endpoint(
        feed: Optional[str] = None,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return list_alias_crossrefs(db, feed=feed, limit=limit)

    response = TestClient(self_test_app).get("/api/advisories/alias-crossref")
    payload = response.json()
    assert response.status_code == 200
    assert payload["count"] == 1
    assert payload["aliases"][0]["alias"] == "GHSA-xxxx"
    assert len(payload["aliases"][0]["advisory_ids"]) == 2
    assert all(
        entry["alias"] != "CVE-yyyy" for entry in payload["aliases"]
    )
    print("PASS")