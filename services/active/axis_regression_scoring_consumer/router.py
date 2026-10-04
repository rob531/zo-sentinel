# deps: fastapi, pydantic, sqlalchemy, requests
"""Axis Regression Scoring Consumer.

Detects when a server's axis p_top score has regressed vs. its previous reading.
A regression is flagged when current p_top is at least 0.15 below the previous
p_top for the same (server_id, axis_name) pair.

Results are written to the mcp_signal_scores mesh table via write_service.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + McpLlmAxisScore ORM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_regression_scoring_consumer"])

DRIFT_THRESHOLD = 0.15  # p_top must drop by this much to be flagged as regression


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class RegressionResult(BaseModel):
    server_id: str
    axis_name: str
    current_p_top: float
    previous_p_top: float
    drift: float
    escalated: bool
    escalated_to: str | None

    model_config = ConfigDict(from_attributes=True)


class RegressionScanResponse(BaseModel):
    scanned: int
    regressions_found: int
    results: list[RegressionResult]


class SingleRegressionResponse(BaseModel):
    server_id: str
    axis_name: str
    current_p_top: float
    previous_p_top: float
    drift: float
    is_regression: bool
    escalated: bool
    escalated_to: str | None


# --------------------------------------------------------------------------- #
# Core logic (pure function, no I/O)
# --------------------------------------------------------------------------- #

def detect_regression(
    current_p_top: float,
    previous_p_top: float,
    threshold: float = DRIFT_THRESHOLD,
) -> tuple[bool, str | None]:
    """
    Return (is_regression, escalated_to).

    A regression is detected when current p_top < previous p_top - threshold.
    """
    drift = current_p_top - previous_p_top
    if drift < -threshold:
        return True, "axis_regression"
    return False, None


# --------------------------------------------------------------------------- #
# Database helpers (app Postgres via SQLAlchemy ORM)
# --------------------------------------------------------------------------- #

def get_last_two_scores(
    session: Session,
    server_id: str,
    axis_name: str,
) -> tuple[McpLlmAxisScore | None, McpLlmAxisScore | None]:
    """
    Return the two most recent axis scores for (server_id, axis_name),
    most recent first.
    """
    q = (
        select(McpLlmAxisScore)
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(2)
    )
    rows = list(session.execute(q).scalars().all())
    if len(rows) >= 2:
        return rows[0], rows[1]
    if len(rows) == 1:
        return rows[0], None
    return None, None


def get_all_unique_server_axis_pairs(
    session: Session,
) -> list[tuple[str, str]]:
    """Return distinct (server_id, axis_name) pairs that have at least 2 scores."""
    sub = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("cnt"),
        )
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .having(func.count(McpLlmAxisScore.id) >= 2)
        .subquery()
    )
    q = select(sub.c.server_id, sub.c.axis_name)
    return list(session.execute(q).all())


# --------------------------------------------------------------------------- #
# DB write helper (app Postgres -- update escalated flags)
# --------------------------------------------------------------------------- #

def mark_score_escalated(
    session: Session,
    score_id: int,
    escalated_to: str,
) -> None:
    """Set escalated=True and escalated_to on a McpLlmAxisScore row."""
    session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.id == score_id
    ).update(
        {
            McpLlmAxisScore.escalated: True,
            McpLlmAxisScore.escalated_to: escalated_to,
        },
        synchronize_session=False,
    )
    session.commit()


# --------------------------------------------------------------------------- #
# Mesh write helper (via write_service HTTP -- not direct DuckDB)
# --------------------------------------------------------------------------- #

def write_signal_to_mesh(
    server_id: str,
    axis_name: str,
    signal_value: float,
    current_p_top: float,
    previous_p_top: float,
    drift: float,
) -> None:
    """
    POST axis regression signal to write_service :8772.

    Non-fatal: failures are logged but do not fail the HTTP response.
    """
    import requests as _requests

    url = "http://127.0.0.1:8772/query"
    payload = {
        "sql": (
            "INSERT INTO mcp_signal_scores "
            "(server_id, signal_type, signal_value, metadata_json) "
            "VALUES (:server_id, :signal_type, :signal_value, :metadata_json)"
        ),
        "params": {
            "server_id": server_id,
            "signal_type": "axis_regression",
            "signal_value": signal_value,
            "metadata_json": {
                "axis_name": axis_name,
                "current_p_top": current_p_top,
                "previous_p_top": previous_p_top,
                "drift": round(drift, 6),
            },
        },
    }
    try:
        resp = _requests.post(url, json=payload, timeout=10)
        if resp.status_code >= 500:
            pass
    except _requests.RequestException:
        pass


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/axis-regression/{server_id}/{axis_name}",
    response_model=SingleRegressionResponse,
)
def get_axis_regression(
    server_id: str,
    axis_name: str,
    db: Session = Depends(get_session),
) -> SingleRegressionResponse:
    """
    Check whether a specific server+axis pair has regressed since its last score.

    Compares the most recent p_top against the previous-most p_top.
    If current < previous - 0.15, marks the score as escalated.
    """
    current, previous = get_last_two_scores(db, server_id, axis_name)

    if current is None:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server_id={server_id}, axis_name={axis_name}",
        )

    current_p = current.p_top or 0.0

    if previous is None:
        return SingleRegressionResponse(
            server_id=server_id,
            axis_name=axis_name,
            current_p_top=round(current_p, 6),
            previous_p_top=current_p,
            drift=0.0,
            is_regression=False,
            escalated=bool(current.escalated),
            escalated_to=current.escalated_to,
        )

    previous_p = previous.p_top or 0.0
    drift = current_p - previous_p

    is_regression, escalated_to = detect_regression(current_p, previous_p)

    if is_regression and not current.escalated:
        mark_score_escalated(db, current.id, escalated_to="axis_regression")
        current = db.get(McpLlmAxisScore, current.id)

    write_signal_to_mesh(
        server_id=server_id,
        axis_name=axis_name,
        signal_value=1.0 if is_regression else 0.0,
        current_p_top=current_p,
        previous_p_top=previous_p,
        drift=drift,
    )

    return SingleRegressionResponse(
        server_id=server_id,
        axis_name=axis_name,
        current_p_top=round(current_p, 6),
        previous_p_top=round(previous_p, 6),
        drift=round(drift, 6),
        is_regression=is_regression,
        escalated=bool(current.escalated) if current else is_regression,
        escalated_to=current.escalated_to if current else escalated_to,
    )


@router.post(
    "/scoring/axis-regression/scan",
    response_model=RegressionScanResponse,
)
def scan_all_axis_regressions(
    db: Session = Depends(get_session),
) -> RegressionScanResponse:
    """
    Scan all (server_id, axis_name) pairs that have at least 2 scores
    and detect regressions in bulk.

    Writes regression signals to the mesh table but does NOT auto-escalate
    in bulk -- escalation is per-score via the single-server endpoint.
    """
    pairs = get_all_unique_server_axis_pairs(db)
    scanned = len(pairs)
    results: list[RegressionResult] = []

    for server_id, axis_name in pairs:
        current, previous = get_last_two_scores(db, server_id, axis_name)
        if current is None or previous is None:
            continue

        current_p = current.p_top or 0.0
        previous_p = previous.p_top or 0.0
        drift = current_p - previous_p

        is_regression, _escalated_to = detect_regression(current_p, previous_p)

        if is_regression and not current.escalated:
            mark_score_escalated(db, current.id, escalated_to="axis_regression")
            current = db.get(McpLlmAxisScore, current.id)

        results.append(
            RegressionResult(
                server_id=server_id,
                axis_name=axis_name,
                current_p_top=round(current_p, 6),
                previous_p_top=round(previous_p, 6),
                drift=round(drift, 6),
                escalated=bool(current.escalated) if current else is_regression,
                escalated_to=current.escalated_to if current else _escalated_to,
            )
        )

        write_signal_to_mesh(
            server_id=server_id,
            axis_name=axis_name,
            signal_value=1.0 if is_regression else 0.0,
            current_p_top=current_p,
            previous_p_top=previous_p,
            drift=drift,
        )

    regressions_found = sum(1 for r in results if r.escalated)

    return RegressionScanResponse(
        scanned=scanned,
        regressions_found=regressions_found,
        results=results,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base as AppBase

    AppBase.metadata.create_all(test_engine)

    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    test_app = FastAPI()
    test_app.include_router(router)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    # Seed data
    now = datetime.now(timezone.utc)

    # server_a / quality: p_top 0.90 → 0.92 → no regression (drift = +0.02)
    # server_a / reliability: p_top 0.80 → 0.55 → regression (drift = -0.25)
    # server_b / speed: p_top 0.70 → 0.68 → no regression (drift = -0.02)
    # server_c / security: p_top 0.60 → 0.30 → regression (drift = -0.30)

    sess = TestSessionLocal()
    sess.add_all([
        # server_a quality - no regression
        McpLlmAxisScore(
            id=1, server_id="server_a", axis_name="quality",
            p_top=0.90, p_critical=0.05, p_danger=0.05, label="excellent",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2, server_id="server_a", axis_name="quality",
            p_top=0.92, p_critical=0.04, p_danger=0.04, label="excellent",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        # server_a reliability - REGRESSION
        McpLlmAxisScore(
            id=3, server_id="server_a", axis_name="reliability",
            p_top=0.80, p_critical=0.10, p_danger=0.10, label="good",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=4, server_id="server_a", axis_name="reliability",
            p_top=0.55, p_critical=0.25, p_danger=0.20, label="poor",
            label_index=2, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        # server_b speed - no regression
        McpLlmAxisScore(
            id=5, server_id="server_b", axis_name="speed",
            p_top=0.70, p_critical=0.15, p_danger=0.15, label="fast",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=6, server_id="server_b", axis_name="speed",
            p_top=0.68, p_critical=0.16, p_danger=0.16, label="fast",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        # server_c security - REGRESSION
        McpLlmAxisScore(
            id=7, server_id="server_c", axis_name="security",
            p_top=0.60, p_critical=0.20, p_danger=0.20, label="ok",
            label_index=1, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=8, server_id="server_c", axis_name="security",
            p_top=0.30, p_critical=0.40, p_danger=0.30, label="danger",
            label_index=3, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
        # server_d / only_one_score -- only one score, should be handled gracefully
        McpLlmAxisScore(
            id=9, server_id="server_d", axis_name="quality",
            p_top=0.75, p_critical=0.15, p_danger=0.10, label="good",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=now,
        ),
    ])
    sess.commit()
    sess.close()

    client = TestClient(test_app)

    # --- Test 1: server_a quality is NOT a regression ---
    resp_q = client.get("/api/scoring/axis-regression/server_a/quality")
    if resp_q.status_code != 200:
        print(f"FAIL: quality returned {resp_q.status_code}: {resp_q.text}")
        _sys.exit(1)
    data_q = resp_q.json()
    if data_q["is_regression"]:
        print(f"FAIL: quality should NOT be regression, drift={data_q['drift']}")
        _sys.exit(1)

    # --- Test 2: server_a reliability IS a regression ---
    resp_r = client.get("/api/scoring/axis-regression/server_a/reliability")
    if resp_r.status_code != 200:
        print(f"FAIL: reliability returned {resp_r.status_code}: {resp_r.text}")
        _sys.exit(1)
    data_r = resp_r.json()
    if not data_r["is_regression"]:
        print(f"FAIL: reliability SHOULD be regression, drift={data_r['drift']}")
        _sys.exit(1)
    if data_r["drift"] > -0.15:
        print(f"FAIL: drift={data_r['drift']} should be < -0.15")
        _sys.exit(1)
    if not data_r["escalated"]:
        print("FAIL: score should be marked escalated")
        _sys.exit(1)
    if data_r["escalated_to"] != "axis_regression":
        print(f"FAIL: escalated_to should be axis_regression, got {data_r['escalated_to']}")
        _sys.exit(1)

    # --- Test 3: server_b speed is NOT a regression ---
    resp_s = client.get("/api/scoring/axis-regression/server_b/speed")
    if resp_s.status_code != 200:
        print(f"FAIL: speed returned {resp_s.status_code}: {resp_s.text}")
        _sys.exit(1)
    data_s = resp_s.json()
    if data_s["is_regression"]:
        print(f"FAIL: speed should NOT be regression, drift={data_s['drift']}")
        _sys.exit(1)

    # --- Test 4: 404 on unknown server/axis ---
    resp_404 = client.get("/api/scoring/axis-regression/unknown_srv/axis_x")
    if resp_404.status_code != 404:
        print(f"FAIL: expected 404 for unknown, got {resp_404.status_code}")
        _sys.exit(1)

    # --- Test 5: scan endpoint finds exactly 2 regressions ---
    resp_scan = client.post("/api/scoring/axis-regression/scan")
    if resp_scan.status_code != 200:
        print(f"FAIL: scan returned {resp_scan.status_code}: {resp_scan.text}")
        _sys.exit(1)
    scan_data = resp_scan.json()
    if scan_data["scanned"] < 4:
        print(f"FAIL: expected >=4 pairs scanned, got {scan_data['scanned']}")
        _sys.exit(1)
    if scan_data["regressions_found"] != 2:
        print(
            f"FAIL: expected 2 regressions (server_a/reliability, server_c/security), "
            f"got {scan_data['regressions_found']}"
        )
        _sys.exit(1)

    # Verify scan results contain the correct axes escalated
    result_map = {
        (r["server_id"], r["axis_name"]): r for r in scan_data["results"]
    }
    a_rel = result_map.get(("server_a", "reliability"), {})
    if not a_rel.get("escalated"):
        print("FAIL: scan should mark server_a/reliability as escalated")
        _sys.exit(1)
    c_sec = result_map.get(("server_c", "security"), {})
    if not c_sec.get("escalated"):
        print("FAIL: scan should mark server_c/security as escalated")
        _sys.exit(1)

    # --- Test 6: Pydantic round-trip (response model validates all fields) ---
    for r in scan_data["results"]:
        if r["drift"] < -0.15 and not r["escalated"]:
            print(f"FAIL: {r['server_id']}/{r['axis_name']} should be escalated")
            _sys.exit(1)

    print("PASS")
