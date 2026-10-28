from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


class Transition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int


class ChangeTypeTaxonomy(BaseModel):
    change_type: Optional[str]
    count: int
    unseen_count: int
    transitions: List[Transition]


class TaxonomyResponse(BaseModel):
    days: int
    by_change_type: List[ChangeTypeTaxonomy]
    total: int


def get_taxonomy(session: Session, days: int) -> TaxonomyResponse:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    rows = session.execute(
        text(
            """
            SELECT
                pe.change_type AS change_type,
                pe.old_tier AS old_tier,
                pe.new_tier AS new_tier,
                pe.seen AS seen
            FROM perspective_events AS pe
            INNER JOIN perspectives AS p
                ON p.id = pe.perspective_id
            WHERE pe.created_at >= :cutoff
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    grouped = {}
    total = 0

    for row in rows:
        change_type = row["change_type"]
        key = (change_type,)
        if key not in grouped:
            grouped[key] = {
                "change_type": change_type,
                "count": 0,
                "unseen_count": 0,
                "transitions": {},
            }

        item = grouped[key]
        item["count"] += 1
        total += 1

        if row["seen"] is not True:
            item["unseen_count"] += 1

        transition_key = (row["old_tier"], row["new_tier"])
        item["transitions"][transition_key] = (
            item["transitions"].get(transition_key, 0) + 1
        )

    by_change_type = []
    for item in grouped.values():
        transitions = [
            Transition(
                old_tier=old_tier,
                new_tier=new_tier,
                count=count,
            )
            for (old_tier, new_tier), count in item["transitions"].items()
        ]
        by_change_type.append(
            ChangeTypeTaxonomy(
                change_type=item["change_type"],
                count=item["count"],
                unseen_count=item["unseen_count"],
                transitions=transitions,
            )
        )

    by_change_type.sort(
        key=lambda entry: (-entry.count, entry.change_type or "")
    )

    return TaxonomyResponse(
        days=days,
        by_change_type=by_change_type,
        total=total,
    )


def create_app() -> FastAPI:
    application = FastAPI()

    @application.get(
        "/api/perspectives/changes/taxonomy",
        response_model=TaxonomyResponse,
    )
    def perspective_change_taxonomy(
        days: int = Query(default=30, ge=1),
        session: Session = Depends(get_session),
    ) -> TaxonomyResponse:
        return get_taxonomy(session, days)

    return application


app = create_app()


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER,
                    name VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id INTEGER,
                    server_id INTEGER,
                    change_type VARCHAR,
                    old_tier VARCHAR,
                    new_tier VARCHAR,
                    seen BOOLEAN,
                    created_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO perspectives (id, org_id, name)
                VALUES (1, 1, 'Test Perspective')
                """
            )
        )

        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO perspective_events
                    (id, perspective_id, server_id, change_type,
                     old_tier, new_tier, seen, created_at)
                VALUES
                    (1, 1, 101, 'tier_change', 'bronze', 'silver', 1, :created_at),
                    (2, 1, 102, 'tier_change', 'bronze', 'silver', 0, :created_at),
                    (3, 1, 103, 'ownership_change', 'silver', 'gold', 1, :created_at)
                """
            ),
            {"created_at": created_at},
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        response = TestClient(test_app).get(
            "/api/perspectives/changes/taxonomy",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert any(
            item["change_type"] == "tier_change" and item["count"] == 2
            for item in payload["by_change_type"]
        )
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()