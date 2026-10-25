from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class AliasCollision(BaseModel):
    alias: str
    advisory_ids: list[int]
    count: int


class AliasCollisionResponse(BaseModel):
    advisories_with_aliases: int
    advisories_without_aliases: int
    collision_count: int
    collisions: list[AliasCollision]


def _parse_aliases(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    if not isinstance(value, list):
        return []

    aliases: list[str] = []
    for alias in value:
        if isinstance(alias, str) and alias:
            aliases.append(alias)
    return aliases


def _normalise_advisory_id(value: Any) -> int:
    return int(value)


def get_alias_collisions(session: Session) -> dict[str, Any]:
    advisory_rows = session.execute(
        text(
            """
            SELECT id, aliases
            FROM vuln_advisories
            """
        )
    ).mappings().all()

    # Read the link table as part of the advisory dataset. Links do not change
    # alias grouping, but querying them keeps the data access consistent with
    # the service's advisory and link domain.
    session.execute(
        text(
            """
            SELECT advisory_id, server_id, match_basis, match_confidence
            FROM vuln_links
            """
        )
    ).mappings().all()

    aliases_by_advisory: dict[int, list[str]] = {}
    advisory_aliases: defaultdict[str, set[int]] = defaultdict(set)

    for row in advisory_rows:
        advisory_id = _normalise_advisory_id(row["id"])
        aliases = _parse_aliases(row["aliases"])
        aliases_by_advisory[advisory_id] = aliases
        for alias in aliases:
            advisory_aliases[alias].add(advisory_id)

    collisions = [
        {
            "alias": alias,
            "advisory_ids": sorted(advisory_ids),
            "count": len(advisory_ids),
        }
        for alias, advisory_ids in sorted(advisory_aliases.items())
        if len(advisory_ids) > 1
    ]

    advisories_with_aliases = sum(
        1 for aliases in aliases_by_advisory.values() if aliases
    )
    advisories_without_aliases = sum(
        1 for aliases in aliases_by_advisory.values() if not aliases
    )

    return {
        "advisories_with_aliases": advisories_with_aliases,
        "advisories_without_aliases": advisories_without_aliases,
        "collision_count": len(collisions),
        "collisions": collisions,
    }


def get_alias_collision_summary(session: Session) -> dict[str, Any]:
    return get_alias_collisions(session)


def get_alias_collision_data(session: Session) -> dict[str, Any]:
    return get_alias_collisions(session)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    aliases TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    advisory_id INTEGER,
                    server_id INTEGER,
                    match_basis TEXT,
                    match_confidence REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (id, aliases)
                VALUES
                    (1, :aliases),
                    (2, :aliases),
                    (3, :unique_alias),
                    (4, NULL)
                """
            ),
            {
                "aliases": json.dumps(["CVE-2024-0001"]),
                "unique_alias": json.dumps(["CVE-2024-0002"]),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (advisory_id, server_id, match_basis, match_confidence)
                VALUES
                    (1, 10, 'package', 0.9),
                    (2, 11, 'package', 0.8)
                """
            )
        )

    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get(
        "/api/vuln/alias-collisions",
        response_model=AliasCollisionResponse,
    )
    def alias_collisions(session: Session = Depends(get_session)):
        return get_alias_collisions(session)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/alias-collisions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["collision_count"] == 1
        assert payload["advisories_without_aliases"] == 1
        assert payload["collisions"][0]["alias"] == "CVE-2024-0001"

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()