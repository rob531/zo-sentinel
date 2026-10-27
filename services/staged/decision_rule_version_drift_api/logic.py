from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def get_decision_rule_version_drift(
    session: Session = Depends(get_session),
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    table_name = McpLlmAxisScore.__tablename__
    sql = text(
        f"""
        SELECT
            axis_name,
            COALESCE(decision_rule_version, '') AS decision_rule_version,
            COALESCE(model_version, '') AS model_version,
            COUNT(*) AS count
        FROM {table_name}
        WHERE axis_name IS NOT NULL
          AND (:axis_name IS NULL OR axis_name = :axis_name)
        GROUP BY axis_name, decision_rule_version, model_version
        ORDER BY axis_name, decision_rule_version, model_version
        """
    )

    rows = session.execute(sql, {"axis_name": axis_name}).mappings().all()

    axes_data: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        current_axis = str(row["axis_name"])
        decision_version = str(row["decision_rule_version"] or "")
        model_version = str(row["model_version"] or "")
        count = int(row["count"])

        axis = axes_data.setdefault(
            current_axis,
            {
                "row_count": 0,
                "buckets": {},
                "null_version_rows": 0,
            },
        )
        axis["row_count"] += count

        if decision_version == "":
            axis["null_version_rows"] += count
            continue

        bucket_key = (decision_version, model_version)
        axis["buckets"][bucket_key] = axis["buckets"].get(bucket_key, 0) + count

    axes: List[Dict[str, Any]] = []
    overall_null_version_rows = 0

    for name in sorted(axes_data):
        axis = axes_data[name]
        row_count = int(axis["row_count"])
        null_version_rows = int(axis["null_version_rows"])
        overall_null_version_rows += null_version_rows

        versions = [
            {
                "decision_rule_version": decision_version,
                "model_version": model_version,
                "count": count,
                "pct": float(count * 100.0 / row_count) if row_count else 0.0,
            }
            for (decision_version, model_version), count in axis["buckets"].items()
        ]
        versions.sort(
            key=lambda version: (
                version["decision_rule_version"],
                version["model_version"],
            ),
            reverse=True,
        )

        if versions:
            newest_version = max(
                version["decision_rule_version"] for version in versions
            )
            newest_count = sum(
                version["count"]
                for version in versions
                if version["decision_rule_version"] == newest_version
            )
            newest_pct = (
                float(newest_count * 100.0 / row_count) if row_count else 0.0
            )
        else:
            newest_version = ""
            newest_pct = 0.0

        axes.append(
            {
                "axis_name": name,
                "row_count": row_count,
                "versions": versions,
                "newest_version": newest_version,
                "newest_pct": newest_pct,
                "null_version_rows": null_version_rows,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes,
        "overall_null_version_rows": overall_null_version_rows,
    }


get_decision_rule_drift = get_decision_rule_version_drift
get_version_drift = get_decision_rule_version_drift


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    axis_name TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (axis_name, decision_rule_version, model_version)
                VALUES
                    (:axis_name, :decision_rule_version, :model_version)
                """
            ),
            [
                {
                    "axis_name": "axis-a",
                    "decision_rule_version": "v1",
                    "model_version": "model-1",
                },
                {
                    "axis_name": "axis-a",
                    "decision_rule_version": "v1",
                    "model_version": "model-1",
                },
                {
                    "axis_name": "axis-a",
                    "decision_rule_version": "v2",
                    "model_version": "model-2",
                },
                {
                    "axis_name": "axis-b",
                    "decision_rule_version": "v1",
                    "model_version": "model-1",
                },
                {
                    "axis_name": "axis-b",
                    "decision_rule_version": "v2",
                    "model_version": "model-2",
                },
                {
                    "axis_name": "axis-b",
                    "decision_rule_version": "v2",
                    "model_version": "model-2",
                },
                {
                    "axis_name": "axis-b",
                    "decision_rule_version": None,
                    "model_version": "model-2",
                },
            ],
        )

    that_app = FastAPI()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/scoring/decision-rule-drift")
    def decision_rule_version_drift_endpoint(
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_decision_rule_version_drift(session)

    response = TestClient(that_app).get(
        "/api/scoring/decision-rule-drift"
    )
    assert response.status_code == 200
    payload = response.json()

    axes = {axis["axis_name"]: axis for axis in payload["axes"]}
    assert axes["axis-a"]["row_count"] == 3
    assert axes["axis-a"]["null_version_rows"] == 0
    assert axes["axis-a"]["newest_version"] == "v2"
    assert axes["axis-a"]["newest_pct"] == pytest_approx(100.0 / 3.0)

    axis_a_buckets = {
        (
            version["decision_rule_version"],
            version["model_version"],
        ): version
        for version in axes["axis-a"]["versions"]
    }
    assert axis_a_buckets[("v1", "model-1")]["count"] == 2
    assert axis_a_buckets[("v1", "model-1")]["pct"] == pytest_approx(200.0 / 3.0)
    assert axis_a_buckets[("v2", "model-2")]["count"] == 1

    assert axes["axis-b"]["row_count"] == 4
    assert axes["axis-b"]["null_version_rows"] == 1
    assert axes["axis-b"]["newest_version"] == "v2"
    assert axes["axis-b"]["newest_pct"] == 50.0
    assert payload["overall_null_version_rows"] == 1

    print("PASS")


def pytest_approx(expected: float, tolerance: float = 1e-9) -> Any:
    class _Approx:
        def __eq__(self, other: Any) -> bool:
            return abs(float(other) - expected) <= tolerance

    return _Approx()


if __name__ == "__main__":
    _run_self_test()