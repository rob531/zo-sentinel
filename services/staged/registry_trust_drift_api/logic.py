"""logic.py -- registry trust drift computation over the real application models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _is_newer_score(candidate: McpLlmAxisScore, current: McpLlmAxisScore) -> bool:
    """Return whether candidate is the newer of two scores for one axis."""
    candidate_time = candidate.scored_at
    current_time = current.scored_at

    if candidate_time is None:
        return False
    if current_time is None:
        return True

    try:
        if candidate_time > current_time:
            return True
        if candidate_time < current_time:
            return False
    except TypeError:
        # Mixed naive/aware datetimes are treated as equal and the id breaks
        # the tie deterministically.
        pass

    return int(candidate.id or 0) > int(current.id or 0)


def _scaled_p_top(value: Any) -> float:
    """Convert a model probability to its 0--100 representation."""
    number = float(value)
    return number * 100.0


def get_trust_drift(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Compute published trust drift against the latest seven-axis scores."""
    limit = max(1, int(limit))

    servers = db.execute(
        select(McpServerRegistry)
        .order_by(McpServerRegistry.server_id)
        .limit(limit)
    ).scalars().all()

    server_ids = [server.server_id for server in servers]
    latest_scores: Dict[tuple, McpLlmAxisScore] = {}

    if server_ids:
        score_rows = db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id.in_(server_ids),
                McpLlmAxisScore.axis_name.in_(AXIS_NAMES),
            )
        ).scalars().all()

        for score in score_rows:
            key = (score.server_id, score.axis_name)
            current = latest_scores.get(key)
            if current is None or _is_newer_score(score, current):
                latest_scores[key] = score

    result_servers: List[Dict[str, Any]] = []
    band_counts = {"aligned": 0, "minor": 0, "severe": 0}

    for server in servers:
        axis_scores = [
            latest_scores[(server.server_id, axis_name)]
            for axis_name in AXIS_NAMES
            if (server.server_id, axis_name) in latest_scores
        ]
        usable_scores = [score for score in axis_scores if score.p_top is not None]

        axes_present = len(usable_scores)
        if usable_scores:
            implied_overall = sum(
                _scaled_p_top(score.p_top) for score in usable_scores
            ) / len(usable_scores)
            drift = abs(float(server.trust_score or 0.0) - implied_overall)
            if drift <= 5:
                drift_band = "aligned"
            elif drift <= 15:
                drift_band = "minor"
            else:
                drift_band = "severe"
        else:
            implied_overall = None
            drift = None
            drift_band = "aligned"

        band_counts[drift_band] += 1
        result_servers.append(
            {
                "server_id": server.server_id,
                "name": server.name,
                "trust_score": server.trust_score,
                "implied_overall": implied_overall,
                "drift": drift,
                "drift_band": drift_band,
                "risk_tier": server.risk_tier,
                "axes_present": axes_present,
                "last_assessed": server.last_assessed,
            }
        )

    return {
        "servers": result_servers,
        "count": len(result_servers),
        "band_counts": band_counts,
    }


# Compatibility names for callers that use the service's descriptive function
# name rather than the endpoint-oriented name.
get_registry_trust_drift = get_trust_drift
list_trust_drift = get_trust_drift


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/trust-drift")
    def trust_drift_endpoint(
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return get_trust_drift(db, limit)

    server_specs = (
        ("aligned-server", "Aligned server", 80.0, 0.80, "aligned"),
        ("minor-server", "Minor drift server", 70.0, 0.80, "minor"),
        ("severe-server", "Severe drift server", 50.0, 0.80, "severe"),
    )

    with SessionLocal() as session:
        score_id = 1
        for server_id, name, trust_score, p_top, _band in server_specs:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    trust_score=trust_score,
                    risk_tier="medium",
                    last_assessed=datetime(2025, 1, 1),
                    verdict="approved",
                )
            )
            for axis_name in AXIS_NAMES:
                session.add(
                    McpLlmAxisScore(
                        id=score_id,
                        server_id=server_id,
                        axis_name=axis_name,
                        model_version="self-test",
                        decision_rule_version="self-test",
                        p_top=p_top,
                        label="test",
                    )
                )
                score_id += 1
        session.commit()

    response = TestClient(test_app).get("/api/registry/trust-drift")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert payload["band_counts"]["severe"] == 1
    severe = next(
        server for server in payload["servers"] if server["server_id"] == "severe-server"
    )
    assert abs(severe["drift"] - 30.0) < 1e-9
    print("PASS")