from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


_BAND_LABELS = [f"{lower}-{lower + 10}" for lower in range(0, 100, 10)]


def get_trust_band_histogram(
    registry_source: Optional[str] = None,
    db: Session = Depends(get_session),
) -> dict:
    statement = select(
        McpServerRegistry.trust_score,
        McpServerRegistry.registry_source,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).all()
    scored_rows = [
        (float(score), source)
        for score, source in rows
        if score is not None
    ]
    unscored_count = sum(1 for score, _ in rows if score is None)
    total = len(scored_rows)

    counts = [0] * 10
    source_counts: dict[str, int] = {}
    scores: list[float] = []

    for score, source in scored_rows:
        band_index = min(max(int(score // 10), 0), 9)
        counts[band_index] += 1
        scores.append(score)
        source_name = source if source is not None else "unknown"
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

    if scores:
        midpoint = len(scores) // 2
        if len(scores) % 2:
            median_trust_score = scores[midpoint]
        else:
            median_trust_score = (scores[midpoint - 1] + scores[midpoint]) / 2
    else:
        median_trust_score = None

    bands = [
        {
            "band": label,
            "count": count,
            "pct": round((count / total) * 100, 2) if total else 0.0,
        }
        for label, count in zip(_BAND_LABELS, counts)
    ]

    return {
        "total": total,
        "bands": bands,
        "by_registry_source": [
            {"registry_source": source, "count": count}
            for source, count in sorted(source_counts.items())
        ],
        "median_trust_score": median_trust_score,
        "unscored_count": unscored_count,
    }


get_registry_trust_band_histogram = get_trust_band_histogram
registry_trust_band_histogram = get_trust_band_histogram


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(bind=engine)

    now = datetime.now(timezone.utc)
    fixtures = [
        ("test-1", "Server One", "pypi", 5.0),
        ("test-2", "Server Two", "npm", 42.0),
        ("test-3", "Server Three", "pypi", 77.5),
        ("test-4", "Server Four", "npm", 95.0),
        ("test-5", "Server Five", "pypi", None),
    ]

    with Session(engine) as session:
        for server_id, name, source, score in fixtures:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    registry_source=source,
                    url=f"https://example.test/{server_id}",
                    trust_score=score,
                    verdict="trusted",
                    confidence=0.9,
                    risk_tier="low",
                    scan_count=1,
                    last_assessed=now,
                    first_seen=now,
                    last_seen=now,
                )
            )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/trust-band-histogram")
    def endpoint(
        registry_source: Optional[str] = None,
        db: Session = Depends(get_session),
    ) -> dict:
        return get_trust_band_histogram(
            registry_source=registry_source,
            db=db,
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/trust-band-histogram")
        assert response.status_code == 200
        payload = response.json()

    assert payload["total"] == 4
    assert payload["unscored_count"] == 1
    assert payload["bands"][9]["band"] == "90-100"
    assert payload["bands"][9]["count"] == 1
    assert [band["band"] for band in payload["bands"]] == [
        "0-10",
        "10-20",
        "20-30",
        "30-40",
        "40-50",
        "50-60",
        "60-70",
        "70-80",
        "80-90",
        "90-100",
    ]
    print("PASS")


if __name__ == "__main__":
    _self_test()