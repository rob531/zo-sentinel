# deps: requests
"""
network_egress_risk_scoring_consumer router.

Scoring consumer that reads network_egress and data_sensitivity axis scores
from mcp_llm_axis_scores, computes a composite egress risk score, and writes
risk_tier back to mcp_server_registry via write_service.

Risk logic: if network_egress.p_top > data_sensitivity.p_top the server
presents elevated exfiltration risk (high egress + low data sensitivity).
"""
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["network_egress_risk_scoring_consumer"])


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class EgressRiskResult(BaseModel):
    server_id: str
    name: str
    network_egress_p_top: Optional[float]
    data_sensitivity_p_top: Optional[float]
    egress_risk_score: float
    risk_tier: str
    updated: bool
    skip_reason: Optional[str] = None


class RunResponse(BaseModel):
    processed: int
    skipped: int
    results: list[EgressRiskResult]


class HealthResponse(BaseModel):
    status: str
    service: str


# ── Core scoring logic ────────────────────────────────────────────────────────

RISK_TIER_THRESHOLDS = [
    (80.0, "HIGH_RISK_ISOLATED"),
    (60.0, "CAUTION_LIMITED"),
    (40.0, "ENTERPRISE_CONTROLLED"),
    (20.0, "TRUSTED_RESEARCH"),
    (5.0, "TRUSTED_GENERAL"),
]


def compute_egress_risk_score(
    network_egress_p_top: Optional[float],
    data_sensitivity_p_top: Optional[float],
) -> float:
    """Compute composite egress risk score 0-100.

    Risk is elevated when egress p_top >> sensitivity p_top (high exfiltration
    potential + low data sensitivity).  Score = (egress - sensitivity) * 100.
    """
    if network_egress_p_top is None or data_sensitivity_p_top is None:
        return 0.0
    if network_egress_p_top <= data_sensitivity_p_top:
        return 0.0
    diff = network_egress_p_top - data_sensitivity_p_top
    return min(100.0, diff * 100.0)


def derive_risk_tier(egress_risk_score: float) -> str:
    """Map composite score to the 6-tier risk tier."""
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if egress_risk_score >= threshold:
            return tier
    return "KNOWN_THREAT"


def write_risk_tier_via_service(server_id: str, risk_tier: str) -> int:
    """Write risk_tier back to mcp_server_registry via write_service."""
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/execute",
            json={
                "sql": (
                    "UPDATE mcp_server_registry "
                    "SET risk_tier = %s, last_assessed = %s "
                    "WHERE server_id = %s"
                ),
                "params": [risk_tier, datetime.now(timezone.utc).isoformat(), server_id],
                "wait": True,
            },
            timeout=10,
        )
        return resp.status_code
    except requests.RequestException:
        return 500


def _get_axis_p_top(
    session: Session, server_id: str, axis_name: str
) -> Optional[float]:
    """Fetch the most recent p_top for a given server/axis."""
    stmt = (
        select(McpLlmAxisScore.p_top)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.axis_name == axis_name)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    )
    return session.scalar(stmt)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="network_egress_risk_scoring_consumer")


@router.post("/run", response_model=RunResponse)
def run(db: Session = Depends(get_session)) -> RunResponse:
    """Compute egress risk for all servers and write updated risk_tier."""
    servers = db.execute(select(McpServerRegistry)).scalars().all()

    results: list[EgressRiskResult] = []
    processed = 0
    skipped = 0

    for server in servers:
        egress_p = _get_axis_p_top(db, server.server_id, "network_egress")
        sens_p = _get_axis_p_top(db, server.server_id, "data_sensitivity")

        score = compute_egress_risk_score(egress_p, sens_p)
        tier = derive_risk_tier(score)

        if egress_p is not None and sens_p is not None and egress_p > sens_p:
            status = write_risk_tier_via_service(server.server_id, tier)
            result = EgressRiskResult(
                server_id=server.server_id,
                name=server.name or "",
                network_egress_p_top=egress_p,
                data_sensitivity_p_top=sens_p,
                egress_risk_score=score,
                risk_tier=tier,
                updated=status == 200,
                skip_reason=None,
            )
            if status == 200:
                processed += 1
            else:
                skipped += 1
        else:
            result = EgressRiskResult(
                server_id=server.server_id,
                name=server.name or "",
                network_egress_p_top=egress_p,
                data_sensitivity_p_top=sens_p,
                egress_risk_score=score,
                risk_tier=tier,
                updated=False,
                skip_reason=(
                    "network_egress p_top <= data_sensitivity p_top"
                    if egress_p is not None and sens_p is not None
                    else "missing axis scores"
                ),
            )
            skipped += 1

        results.append(result)

    return RunResponse(processed=processed, skipped=skipped, results=results)


# ── Self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        test_app = FastAPI()

        def override_get_session():
            db = TestingSession()
            try:
                yield db
            finally:
                db.close()

        test_app.dependency_overrides[get_session] = override_get_session

        # Seed test data
        test_db = TestingSession()
        servers = [
            McpServerRegistry(
                server_id="srv-001",
                name="High Egress Low Sens",
                registry_source="test",
                url="http://srv001.example.com",
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="Low Egress High Sens",
                registry_source="test",
                url="http://srv002.example.com",
            ),
            McpServerRegistry(
                server_id="srv-003",
                name="Both High",
                registry_source="test",
                url="http://srv003.example.com",
            ),
            McpServerRegistry(
                server_id="srv-004",
                name="Both Low",
                registry_source="test",
                url="http://srv004.example.com",
            ),
            McpServerRegistry(
                server_id="srv-005",
                name="Missing Axis Scores",
                registry_source="test",
                url="http://srv005.example.com",
            ),
        ]
        for s in servers:
            test_db.add(s)

        now = datetime.now(timezone.utc)
        axis_data = [
            # server with high egress, low sensitivity → elevated risk
            McpLlmAxisScore(
                server_id="srv-001", axis_name="network_egress",
                p_top=0.85, label="HIGH", label_index=2,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.1, 0.1, 0.8],
                scored_at=now, escalated=False,
            ),
            McpLlmAxisScore(
                server_id="srv-001", axis_name="data_sensitivity",
                p_top=0.15, label="LOW", label_index=0,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.85, 0.1, 0.05],
                scored_at=now, escalated=False,
            ),
            # egress <= sensitivity → skip
            McpLlmAxisScore(
                server_id="srv-002", axis_name="network_egress",
                p_top=0.2, label="LOW", label_index=0,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.8, 0.15, 0.05],
                scored_at=now, escalated=False,
            ),
            McpLlmAxisScore(
                server_id="srv-002", axis_name="data_sensitivity",
                p_top=0.8, label="HIGH", label_index=2,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.05, 0.1, 0.85],
                scored_at=now, escalated=False,
            ),
            # egress > sensitivity but diff < threshold → low score
            McpLlmAxisScore(
                server_id="srv-003", axis_name="network_egress",
                p_top=0.75, label="HIGH", label_index=2,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.1, 0.1, 0.8],
                scored_at=now, escalated=False,
            ),
            McpLlmAxisScore(
                server_id="srv-003", axis_name="data_sensitivity",
                p_top=0.72, label="HIGH", label_index=2,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.1, 0.15, 0.75],
                scored_at=now, escalated=False,
            ),
            # both low
            McpLlmAxisScore(
                server_id="srv-004", axis_name="network_egress",
                p_top=0.1, label="LOW", label_index=0,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.9, 0.08, 0.02],
                scored_at=now, escalated=False,
            ),
            McpLlmAxisScore(
                server_id="srv-004", axis_name="data_sensitivity",
                p_top=0.08, label="LOW", label_index=0,
                model_version="test", decision_rule_version="test",
                adapter_sha256="abc123", probs=[0.92, 0.06, 0.02],
                scored_at=now, escalated=False,
            ),
            # srv-005 has NO axis scores
        ]
        for a in axis_data:
            test_db.add(a)
        test_db.commit()
        test_db.close()

        # Mock write_service to track calls
        write_calls = []
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        original_post = requests.post

        def mock_post(url, **kwargs):
            write_calls.append(kwargs.get("json", {}))
            return mock_resp

        requests.post = mock_post

        try:
            # Wire in the test router
            from app.main import app as main_app

            # Use the test app with the router
            test_app.include_router(router)
            test_app.dependency_overrides[get_session] = override_get_session

            # Fire /run via test client
            from fastapi.testclient import TestClient

            client = TestClient(test_app)
            resp = client.post("/api/run")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

            body = resp.json()
            assert body["processed"] >= 1, f"Expected at least 1 processed, got {body['processed']}"
            assert len(write_calls) == body["processed"], (
                f"Expected {body['processed']} write_service calls, got {len(write_calls)}"
            )

            # Verify specific servers
            srv001 = next(r for r in body["results"] if r["server_id"] == "srv-001")
            assert srv001["network_egress_p_top"] == 0.85, srv001
            assert srv001["data_sensitivity_p_top"] == 0.15, srv001
            assert srv001["updated"] is True, f"srv-001 should be updated: {srv001}"
            # egress 0.85 - sens 0.15 = 0.70 → >= 60 → CAUTION_LIMITED
            assert srv001["risk_tier"] == "CAUTION_LIMITED", srv001

            srv002 = next(r for r in body["results"] if r["server_id"] == "srv-002")
            assert srv002["updated"] is False, "srv-002 should be skipped (egress <= sens)"
            assert srv002["skip_reason"] == "network_egress p_top <= data_sensitivity p_top"

            srv005 = next(r for r in body["results"] if r["server_id"] == "srv-005")
            assert srv005["updated"] is False
            assert srv005["skip_reason"] == "missing axis scores"

            # Health check
            resp_h = client.get("/api/health")
            assert resp_h.status_code == 200
            assert resp_h.json()["status"] == "ok"

            print("PASS")
        finally:
            requests.post = original_post
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
