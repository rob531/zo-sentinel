"""Contract acceptance test for the advisory alias cross-reference API."""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, VulnAdvisory


class AliasEntry(BaseModel):
    alias: str
    advisory_ids: list[str]
    feeds: list[str]
    severities: list[str]


class AliasCrossrefResponse(BaseModel):
    count: int
    aliases: list[AliasEntry]


router = APIRouter(prefix="/api/advisories")


def _alias_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


@router.get(
    "/alias-crossref",
    response_model=AliasCrossrefResponse,
)
def alias_crossref(
    feed: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_session),
) -> AliasCrossrefResponse:
    statement = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.aliases,
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)

    grouped: dict[str, dict[str, Any]] = {}
    for row in db.execute(statement).all():
        advisory_id = str(row.id)
        for alias in set(_alias_values(row.aliases)):
            entry = grouped.setdefault(
                alias,
                {"ids": set(), "feeds": set(), "severities": set()},
            )
            entry["ids"].add(advisory_id)
            if row.feed is not None:
                entry["feeds"].add(str(row.feed))
            if row.severity is not None:
                entry["severities"].add(str(row.severity))

    aliases = []
    for alias in sorted(grouped):
        entry = grouped[alias]
        if len(entry["ids"]) < 2:
            continue
        aliases.append(
            AliasEntry(
                alias=alias,
                advisory_ids=sorted(entry["ids"]),
                feeds=sorted(entry["feeds"]),
                severities=sorted(entry["severities"]),
            )
        )
        if len(aliases) >= limit:
            break

    return AliasCrossrefResponse(count=len(aliases), aliases=aliases)


def run() -> bool:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    try:
        session.add_all(
            [
                VulnAdvisory(
                    id="adv-1",
                    feed="osv",
                    source_url="https://example.test/adv-1",
                    summary="First advisory",
                    severity="HIGH",
                    ecosystem="PyPI",
                    package="example-one",
                    affected_ranges=[],
                    aliases=["GHSA-xxxx"],
                    identities=[],
                    content_hash="hash-1",
                ),
                VulnAdvisory(
                    id="adv-2",
                    feed="github",
                    source_url="https://example.test/adv-2",
                    summary="Second advisory",
                    severity="MEDIUM",
                    ecosystem="npm",
                    package="example-two",
                    affected_ranges=[],
                    aliases=["GHSA-xxxx"],
                    identities=[],
                    content_hash="hash-2",
                ),
                VulnAdvisory(
                    id="adv-3",
                    feed="nvd",
                    source_url="https://example.test/adv-3",
                    summary="Third advisory",
                    severity="LOW",
                    ecosystem="PyPI",
                    package="example-three",
                    affected_ranges=[],
                    aliases=["CVE-yyyy"],
                    identities=[],
                    content_hash="hash-3",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/advisories/alias-crossref")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["count"] == 1, body
    assert len(body["aliases"]) == 1, body
    assert body["aliases"][0]["alias"] == "GHSA-xxxx", body
    assert len(body["aliases"][0]["advisory_ids"]) == 2, body
    assert "CVE-yyyy" not in {
        entry["alias"] for entry in body["aliases"]
    }, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)