from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


HISTOGRAM_EDGES = tuple((lower, lower + 10) for lower in range(0, 100, 10))
MAX_SERVERS_PER_BIN = 50


def _source_sort_key(value: Any) -> tuple[bool, str]:
    return (value is None, str(value) if value is not None else "")


def build_registry_trust_score_histogram(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_assessed,
        )
    ).all()

    bins: list[dict[str, Any]] = [
        {
            "bin": f"{lower}-{upper}",
            "lower": lower,
            "upper": upper,
            "count": 0,
            "servers": [],
        }
        for lower, upper in HISTOGRAM_EDGES
    ]
    null_bin: dict[str, Any] = {
        "bin": "null",
        "lower": None,
        "upper": None,
        "count": 0,
        "servers": [],
    }

    source_counts: dict[Any, int] = {}
    scored = 0
    unscored = 0

    for row in rows:
        server_id = str(row.server_id)
        trust_score = row.trust_score
        source = row.registry_source
        source_counts[source] = source_counts.get(source, 0) + 1

        if trust_score is None:
            unscored += 1
            null_bin["count"] += 1
            if len(null_bin["servers"]) < MAX_SERVERS_PER_BIN:
                null_bin["servers"].append(server_id)
            continue

        scored += 1
        numeric_score = float(trust_score)
        bin_index = min(max(int(numeric_score // 10), 0), len(bins) - 1)
        selected_bin = bins[bin_index]
        selected_bin["count"] += 1
        if len(selected_bin["servers"]) < MAX_SERVERS_PER_BIN:
            selected_bin["servers"].append(server_id)

    bins.append(null_bin)

    by_source = [
        {"registry_source": source, "count": count}
        for source, count in sorted(
            source_counts.items(),
            key=lambda item: _source_sort_key(item[0]),
        )
    ]

    return {
        "total": len(rows),
        "scored": scored,
        "unscored": unscored,
        "bins": bins,
        "by_source": by_source,
    }


def build_trust_score_histogram(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return build_registry_trust_score_histogram(db)


def build_registry_trust_score_histogram_logic(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return build_registry_trust_score_histogram(db)


def get_registry_trust_score_histogram(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return build_registry_trust_score_histogram(db)


def registry_trust_score_histogram(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return build_registry_trust_score_histogram(db)


def build_indicator_lifecycle(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return build_registry_trust_score_histogram(db)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-low",
                    trust_score=0.0,
                    registry_source="test",
                    verdict="trusted",
                    risk_tier="low",
                    last_assessed=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="registry-mid",
                    trust_score=55.5,
                    registry_source="test",
                    verdict="conditional",
                    risk_tier="medium",
                    last_assessed=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="registry-high",
                    trust_score=95.0,
                    registry_source="test",
                    verdict="trusted",
                    risk_tier="low",
                    last_assessed=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="registry-null",
                    trust_score=None,
                    registry_source="test",
                    verdict="unscored",
                    risk_tier="unknown",
                    last_assessed=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/trust-score-histogram")
    def trust_score_histogram(
        result: dict[str, Any] = Depends(build_registry_trust_score_histogram),
    ):
        return result

    response = TestClient(that_app).get(
        "/api/registry/trust-score-histogram"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["scored"] == 3
    assert payload["unscored"] == 1
    assert next(item for item in payload["bins"] if item["bin"] == "50-60")[
        "count"
    ] == 1
    print("PASS")