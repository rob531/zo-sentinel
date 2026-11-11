from statistics import median
from typing import Any

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session


def get_verdict_reasoning_coverage(db: Session = Depends(get_session)) -> dict[str, Any]:
    rows = list(db.execute(select(McpServerRegistry)).scalars().all())

    total = len(rows)
    with_reasoning = 0
    verdict_counts: dict[str, dict[str, int]] = {}
    confidences: list[float] = []
    missing_samples: list[dict[str, str]] = []

    for row in rows:
        reasoning = row.verdict_reasoning
        has_reasoning = reasoning is not None and str(reasoning).strip() != ""
        if has_reasoning:
            with_reasoning += 1

        verdict = "" if row.verdict is None else str(row.verdict)
        verdict_key = verdict if verdict else "unknown"
        counts = verdict_counts.setdefault(
            verdict_key,
            {"total": 0, "with_reasoning": 0},
        )
        counts["total"] += 1
        if has_reasoning:
            counts["with_reasoning"] += 1

        if row.confidence is not None:
            confidences.append(float(row.confidence))

        verdict_is_set = row.verdict is not None and str(row.verdict).strip() != ""
        if verdict_is_set and row.confidence is None:
            missing_samples.append(
                {
                    "server_id": str(row.server_id),
                    "name": str(row.name),
                    "verdict": str(row.verdict),
                }
            )

    if confidences:
        confidence_summary = {
            "min": float(min(confidences)),
            "median": float(median(confidences)),
            "max": float(max(confidences)),
        }
    else:
        confidence_summary = {"min": 0.0, "median": 0.0, "max": 0.0}

    by_verdict = [
        {
            "verdict": verdict,
            "total": counts["total"],
            "with_reasoning": counts["with_reasoning"],
        }
        for verdict, counts in verdict_counts.items()
    ]

    return {
        "total": total,
        "with_reasoning": with_reasoning,
        "without_reasoning": total - with_reasoning,
        "by_verdict": by_verdict,
        "confidence": confidence_summary,
        "missing_samples": missing_samples,
    }


get_verdict_reasoning_coverage_data = get_verdict_reasoning_coverage
get_verdict_reasoning_coverage_summary = get_verdict_reasoning_coverage


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    verdict="approved",
                    verdict_reasoning="Meets policy requirements",
                    confidence=0.9,
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    verdict="review",
                    verdict_reasoning="Requires additional review",
                    confidence=0.7,
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    verdict="approved",
                    verdict_reasoning=None,
                    confidence=None,
                ),
                McpServerRegistry(
                    server_id="server-4",
                    name="Server Four",
                    verdict="denied",
                    verdict_reasoning="",
                    confidence=0.2,
                ),
                McpServerRegistry(
                    server_id="server-5",
                    name="Server Five",
                    verdict="review",
                    verdict_reasoning=None,
                    confidence=0.5,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/verdict-reasoning-coverage")
    def verdict_reasoning_coverage(db: Session = Depends(get_session)):
        return get_verdict_reasoning_coverage(db)

    response = TestClient(that_app).get(
        "/api/registry/verdict-reasoning-coverage"
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 5
    assert payload["with_reasoning"] == 2
    assert len(payload["missing_samples"]) >= 1

    print("PASS")