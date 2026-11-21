"""logic.py -- axis probability entropy computation over the real application data layer."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _parse_probabilities(value: Any) -> List[float]:
    """Return a valid probability list, or an empty list for missing/invalid data."""
    if value is None:
        return []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []

    probabilities: List[float] = []
    for item in value:
        if isinstance(item, bool):
            return []
        try:
            probability = float(item)
        except (TypeError, ValueError):
            return []
        if not math.isfinite(probability) or probability < 0:
            return []
        probabilities.append(probability)

    return probabilities


def _entropy_and_top_share(probabilities: Iterable[float]) -> Tuple[float, float]:
    probabilities = list(probabilities)
    if not probabilities:
        return 0.0, 0.0

    entropy = 0.0
    for probability in probabilities:
        if probability > 0:
            entropy -= probability * math.log2(probability)
    return float(entropy), float(max(probabilities, default=0.0))


def _row_result(row: McpLlmAxisScore) -> Dict[str, Any]:
    probabilities = _parse_probabilities(row.probs)
    entropy, top_share = _entropy_and_top_share(probabilities)

    number_of_labels = len(probabilities)
    if number_of_labels > 1:
        flat = (
            entropy >= 0.95 * math.log2(number_of_labels)
            and top_share < 0.4
        )
    else:
        flat = False

    return {
        "server_id": row.server_id,
        "axis_name": row.axis_name,
        "label": row.label,
        "entropy": entropy,
        "top_share": top_share,
        "flat": bool(flat),
        "escalated": bool(row.escalated) if row.escalated is not None else False,
        "model_version": row.model_version,
        "scored_at": row.scored_at,
    }


def axis_entropy_profile(
    db: Session,
    server_id: Optional[str] = None,
    min_entropy: float = 0.0,
    limit: int = 100,
) -> Dict[str, Any]:
    """Compute the entropy profile for real ``mcp_llm_axis_scores`` rows."""
    statement = select(McpLlmAxisScore)
    if server_id is not None:
        statement = statement.where(McpLlmAxisScore.server_id == server_id)

    rows = db.execute(statement).scalars().all()
    results = [_row_result(row) for row in rows]
    results = [result for result in results if result["entropy"] >= min_entropy]
    results.sort(key=lambda result: result["entropy"], reverse=True)

    if limit is not None:
        results = results[: max(0, int(limit))]

    return {
        "total": len(results),
        "flat_count": sum(1 for result in results if result["flat"]),
        "rows": results,
    }


def get_axis_entropy_rows(
    db: Session,
    server_id: Optional[str] = None,
    min_entropy: float = 0.0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return the filtered and entropy-sorted axis rows."""
    return axis_entropy_profile(
        db,
        server_id=server_id,
        min_entropy=min_entropy,
        limit=limit,
    )["rows"]


compute_axis_entropy = axis_entropy_profile


app = FastAPI()


@app.get("/api/scores/axis-entropy")
def axis_entropy_endpoint(
    server_id: Optional[str] = Query(default=None),
    min_entropy: float = Query(default=0.0),
    limit: int = Query(default=100, ge=1),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return axis_entropy_profile(
        db,
        server_id=server_id,
        min_entropy=min_entropy,
        limit=limit,
    )


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
    McpLlmAxisScore.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    testing_session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-entropy-test",
                axis_name="uniform",
                label="uniform",
                probs=json.dumps([0.34, 0.33, 0.33]),
                p_top=0.34,
                p_critical=0.0,
                p_danger=0.0,
                escalated=False,
                model_version="test-model",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-entropy-test",
                axis_name="peaked",
                label="peaked",
                probs=json.dumps([0.99, 0.005, 0.005]),
                p_top=0.99,
                p_critical=0.0,
                p_danger=0.0,
                escalated=False,
                model_version="test-model",
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-entropy-test",
                axis_name="missing",
                label="missing",
                probs=None,
                p_top=None,
                p_critical=None,
                p_danger=None,
                escalated=False,
                model_version="test-model",
            ),
        ]
    )
    testing_session.commit()

    testing_app = FastAPI()
    testing_app.dependency_overrides[get_session] = lambda: testing_session

    @testing_app.get("/api/scores/axis-entropy")
    def testing_endpoint(
        server_id: Optional[str] = Query(default=None),
        min_entropy: float = Query(default=0.0),
        limit: int = Query(default=100, ge=1),
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return axis_entropy_profile(
            db,
            server_id=server_id,
            min_entropy=min_entropy,
            limit=limit,
        )

    response = TestClient(testing_app).get(
        "/api/scores/axis-entropy",
        params={"server_id": "server-entropy-test", "min_entropy": 0.0, "limit": 100},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["flat_count"] == 1
    peaked = next(row for row in payload["rows"] if row["axis_name"] == "peaked")
    assert peaked["entropy"] < 0.2
    missing = next(row for row in payload["rows"] if row["axis_name"] == "missing")
    assert missing["entropy"] == 0.0
    assert missing["flat"] is False

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()