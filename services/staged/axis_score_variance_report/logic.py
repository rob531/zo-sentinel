from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _median_from_ranked(values: list[tuple[int, float]]) -> float | None:
    if not values:
        return None
    ordered = [value for _, value in sorted(values, key=lambda item: item[0])]
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


def _std_dev(values: list[float]) -> float:
    return float(statistics.pstdev(values)) if values else 0.0


def get_axis_score_variance_report(
    session: Session,
    days: int = 7,
    min_delta: float = 5.0,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")
    if min_delta < 0:
        raise ValueError("min_delta must be non-negative")

    period_end = datetime.now(timezone.utc).replace(tzinfo=None)
    current_start = period_end - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)
    window = case(
        (McpLlmAxisScore.scored_at >= current_start, "current"),
        else_="prior",
    )
    axis_partition = (
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        window,
    )
    overall_partition = (McpLlmAxisScore.server_id, window)
    axis_rank = func.row_number().over(
        partition_by=axis_partition,
        order_by=(
            McpLlmAxisScore.p_top.asc(),
            McpLlmAxisScore.scored_at.asc(),
            McpLlmAxisScore.id.asc(),
        ),
    ).label("axis_rank")
    axis_count = func.count().over(partition_by=axis_partition).label("axis_count")
    overall_rank = func.row_number().over(
        partition_by=overall_partition,
        order_by=(
            McpLlmAxisScore.p_top.asc(),
            McpLlmAxisScore.scored_at.asc(),
            McpLlmAxisScore.id.asc(),
        ),
    ).label("overall_rank")
    overall_count = func.count().over(partition_by=overall_partition).label("overall_count")
    label_rank = func.row_number().over(
        partition_by=axis_partition,
        order_by=(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc()),
    ).label("label_rank")

    rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top.label("score"),
            window.label("window"),
            axis_rank,
            axis_count,
            overall_rank,
            overall_count,
            label_rank,
        )
        .where(
            McpLlmAxisScore.scored_at >= prior_start,
            McpLlmAxisScore.scored_at < period_end,
            McpLlmAxisScore.p_top.is_not(None),
        )
    ).all()

    axis_values: dict[str, dict[str, dict[str, list[tuple[int, float]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    overall_values: dict[str, dict[str, list[tuple[int, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    labels: dict[str, dict[str, str]] = defaultdict(dict)

    for row in rows:
        server_id = str(row.server_id)
        axis_name = str(row.axis_name)
        score = float(row.score)
        bucket = str(row.window)
        axis_values[server_id][axis_name][bucket].append((int(row.axis_rank), score))
        overall_values[server_id][bucket].append((int(row.overall_rank), score))
        if bucket == "current" and row.label_rank == 1 and row.label:
            labels[server_id][axis_name] = str(row.label)

    server_ids = sorted(
        server_id
        for server_id, buckets in axis_values.items()
        if any(axis_buckets.get("current") for axis_buckets in buckets.values())
    )
    names: dict[str, str | None] = {}
    if server_ids:
        for server_id, name in session.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name).where(
                McpServerRegistry.server_id.in_(server_ids)
            )
        ).all():
            names[str(server_id)] = name

    servers: list[dict[str, Any]] = []
    for server_id in server_ids:
        axes: list[dict[str, Any]] = []
        for axis_name in sorted(axis_values[server_id]):
            buckets = axis_values[server_id][axis_name]
            current = buckets.get("current", [])
            if not current:
                continue
            prior_median = _median_from_ranked(buckets.get("prior", []))
            current_median = _median_from_ranked(current)
            median_delta = (
                current_median - prior_median
                if current_median is not None and prior_median is not None
                else 0.0
            )
            axes.append(
                {
                    "axis_name": axis_name,
                    "label": labels[server_id].get(axis_name, axis_name),
                    "std_dev": round(_std_dev([score for _, score in current]), 10),
                    "median_delta": round(float(median_delta), 10),
                    "flagged": prior_median is not None and abs(median_delta) >= min_delta,
                }
            )

        current_overall = overall_values[server_id].get("current", [])
        prior_overall_median = _median_from_ranked(overall_values[server_id].get("prior", []))
        current_overall_median = _median_from_ranked(current_overall)
        overall_delta = (
            current_overall_median - prior_overall_median
            if current_overall_median is not None and prior_overall_median is not None
            else 0.0
        )
        servers.append(
            {
                "server_id": server_id,
                "name": names.get(server_id) or server_id,
                "overall": {
                    "std_dev": round(
                        _std_dev([score for _, score in current_overall]), 10
                    ),
                    "median_delta": round(float(overall_delta), 10),
                    "flagged": prior_overall_median is not None
                    and abs(overall_delta) >= min_delta,
                },
                "axes": axes,
            }
        )

    return {"period": f"last_{days}_days", "servers": servers}


def _run_self_test() -> None:
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        axes = ["accuracy", "relevance", "coherence", "toxicity", "bias"]
        with testing_session() as db:
            servers = [
                McpServerRegistry(server_id="srv1", name="Server One"),
                McpServerRegistry(server_id="srv2", name="Server Two"),
                McpServerRegistry(server_id="srv3", name="Server Three"),
            ]
            db.add_all(servers)
            score_id = 1
            for server in servers:
                for axis_index, axis_name in enumerate(axes):
                    baseline = 20.0 + axis_index
                    shift = (
                        12.0
                        if server.server_id == "srv2" and axis_name == "accuracy"
                        else 0.0
                    )
                    for window_name, scored_at, score in (
                        ("prior", now - timedelta(days=9), baseline),
                        ("current", now - timedelta(days=2), baseline + shift),
                    ):
                        db.add(
                            McpLlmAxisScore(
                                id=score_id,
                                server_id=server.server_id,
                                axis_name=axis_name,
                                label=axis_name.title(),
                                label_index=axis_index,
                                probs={},
                                p_top=score,
                                p_critical=0.0,
                                p_danger=0.0,
                                escalated=False,
                                escalated_to=None,
                                decision_rule_version="variance-self-test-v1",
                                model_version=f"{window_name}-{axis_name}",
                                adapter_sha256="a" * 64,
                                scored_at=scored_at,
                            )
                        )
                        score_id += 1
            db.commit()

        def override_get_session():
            db = testing_session()
            try:
                yield db
            finally:
                db.close()

        test_app = FastAPI()

        @test_app.get("/api/scoring/variance")
        def variance_endpoint(
            days: int = Query(default=7, ge=1),
            min_delta: float = Query(default=5.0, ge=0.0),
            session: Session = Depends(get_session),
        ) -> dict[str, Any]:
            return get_axis_score_variance_report(session, days, min_delta)

        test_app.dependency_overrides[get_session] = override_get_session
        with TestClient(test_app) as client:
            response = client.get(
                "/api/scoring/variance",
                params={"days": 7, "min_delta": 5.0},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["period"] == "last_7_days", payload
        assert len(payload["servers"]) == 3, payload
        assert all(len(server["axes"]) == 5 for server in payload["servers"]), payload
        flagged_servers = [
            server
            for server in payload["servers"]
            if server["overall"]["flagged"]
            or any(axis["flagged"] for axis in server["axes"])
        ]
        assert len(flagged_servers) == 1, payload
        assert flagged_servers[0]["server_id"] == "srv2", payload
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        _run_self_test()
    except Exception as exc:
        print(f"FAIL: {exc!r}", file=sys.stderr)
        sys.exit(1)
    print("PASS")
