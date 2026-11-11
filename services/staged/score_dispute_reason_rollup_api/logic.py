from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, Query
from sqlalchemy import text

from app.db import get_session
from app.models import McpScoreDispute

app = FastAPI()


def _date_filter(from_date: Optional[date], to_date: Optional[date]):
    conditions = []
    parameters = {}

    if from_date is not None:
        conditions.append("created_at >= :from_date")
        parameters["from_date"] = from_date.isoformat()

    if to_date is not None:
        conditions.append("created_at <= :to_date")
        parameters["to_date"] = to_date.isoformat()

    return conditions, parameters


def _fetch_rollup(session, from_date: Optional[date], to_date: Optional[date]):
    conditions, parameters = _date_filter(from_date, to_date)
    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)

    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    dialect_name = getattr(dialect, "name", "")

    if dialect_name == "postgresql":
        query = text(
            """
            SELECT
                reason_category,
                status,
                COUNT(*) AS bucket_count,
                COUNT(*) FILTER (WHERE resolved_at IS NULL) AS pending_count,
                COUNT(resolved_at) AS resolved_rows,
                AVG(
                    EXTRACT(EPOCH FROM (resolved_at - created_at)) / 86400.0
                ) FILTER (WHERE resolved_at IS NOT NULL) AS avg_resolution_days
            FROM mcp_score_disputes
            """
            + where_clause
            + """
            GROUP BY reason_category, status
            ORDER BY COUNT(*) DESC, reason_category ASC
            """
        )
    else:
        query = text(
            """
            SELECT
                reason_category,
                status,
                COUNT(*) AS bucket_count,
                SUM(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved_rows,
                AVG(
                    julianday(resolved_at) - julianday(created_at)
                ) AS avg_resolution_days
            FROM mcp_score_disputes
            """
            + where_clause
            + """
            GROUP BY reason_category, status
            ORDER BY COUNT(*) DESC, reason_category ASC
            """
        )

    return session.execute(query, parameters).mappings().all()


def get_reason_rollup(
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    session=Depends(get_session),
):
    rows = _fetch_rollup(session, from_date, to_date)

    categories = {}
    total = 0
    pending = 0

    for row in rows:
        category_name = str(row["reason_category"] or "")
        bucket_count = int(row["bucket_count"] or 0)
        bucket_pending = int(row["pending_count"] or 0)
        resolved_rows = int(row["resolved_rows"] or 0)
        status = str(row["status"] or "")
        bucket_average = row["avg_resolution_days"]

        category = categories.setdefault(
            category_name,
            {
                "reason_category": category_name,
                "total": 0,
                "pending": 0,
                "resolved": 0,
                "avg_resolution_days": None,
                "_resolution_days": 0.0,
                "_resolved_rows": 0,
            },
        )

        category["total"] += bucket_count
        category["pending"] += bucket_pending
        if status.casefold() == "resolved":
            category["resolved"] += bucket_count

        if bucket_average is not None and resolved_rows:
            category["_resolution_days"] += float(bucket_average) * resolved_rows
            category["_resolved_rows"] += resolved_rows

        total += bucket_count
        pending += bucket_pending

    output_categories = []
    for category in categories.values():
        if category["_resolved_rows"]:
            category["avg_resolution_days"] = (
                category["_resolution_days"] / category["_resolved_rows"]
            )
        else:
            category["avg_resolution_days"] = None

        output_categories.append(
            {
                "reason_category": category["reason_category"],
                "total": category["total"],
                "pending": category["pending"],
                "resolved": category["resolved"],
                "avg_resolution_days": category["avg_resolution_days"],
            }
        )

    output_categories.sort(key=lambda item: (-item["total"], item["reason_category"]))

    return {
        "total": total,
        "pending": pending,
        "categories": output_categories,
    }


app.get("/api/disputes/reason-rollup")(get_reason_rollup)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    proposed_overall_risk REAL,
                    reason_category TEXT,
                    status TEXT,
                    created_at TEXT,
                    resolved_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes
                    (id, server_id, proposed_overall_risk, reason_category,
                     status, created_at, resolved_at)
                VALUES
                    (1, 'server-1', 0.8, 'accuracy', 'resolved',
                     '2024-01-01T00:00:00', '2024-01-03T00:00:00'),
                    (2, 'server-2', 0.6, 'accuracy', 'open',
                     '2024-01-02T00:00:00', NULL),
                    (3, 'server-3', 0.7, 'safety', 'open',
                     '2024-01-03T00:00:00', NULL),
                    (4, 'server-4', 0.4, 'safety', 'disputed',
                     '2024-01-04T00:00:00', '2024-01-06T00:00:00')
                """
            )
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.get("/api/disputes/reason-rollup")(get_reason_rollup)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/reason-rollup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["pending"] == 2
    assert len(payload["categories"]) == 2
    assert payload["categories"][0]["total"] >= payload["categories"][1]["total"]

    print("PASS")