# deps: fastapi, pydantic, sqlalchemy, requests
"""Server Registry Health API.

GET /api/registry/health-summary
  Aggregated health metrics from mcp_server_registry: total count, tier
  distribution, verdict distribution, average trust score, median scan age,
  never-scanned count, high-risk count.

GET /api/servers/freshness/health
  Per-server freshness status: scan age, score age, and a degraded/healthy/unknown
  overall flag, plus stale daemon list from write_service.

Auth: public (no auth required per directive).
Data: app-db via get_session + SQLAlchemy ORM on McpServerRegistry /
  McpLlmAxisScore. Daemon health comes from write_service /query on
  the service_health table.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Generator, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_registry_health_api"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SCAN_STALE_DAYS = 7
SCORE_STALE_DAYS = 7

DAEMON_THRESHOLD_SECONDS: dict[str, int] = {
    "write_service": 300,
    "inference_router": 120,
    "manager_agent": 120,
    "pipeline_bridge": 120,
    "t2_consumer": 120,
    "zo_sentinel_builder": 600,
    "sentinel_directive_generator": 7500,
    "gate_scheduler": 60,
    "self_diagnostics": 600,
    "build_watcher_api": 600,
    "mcp_scanner": 14400,
    "signal_analyser": 120,
    "trust_synthesiser": 600,
    "threat_intel_ingestor": 600,
    "attestation_engine": 600,
    "rug_pull_monitor": 28800,
    "risk_ranker": 600,
    "world_article_feeder": 600,
    "data_velocity": 120,
    "anti_entropy": 14400,
    "wisdom_synthesiser": 14400,
    "gate_orchestrator": 14400,
}


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #

class HealthSummaryResponse(BaseModel):
    total_servers: int = Field(..., description="Total registered servers")
    by_tier: dict[str, int] = Field(..., description="Count per risk tier")
    by_verdict: dict[str, int] = Field(..., description="Count per verdict")
    avg_trust_score: float = Field(..., description="Average trust score (0-1)")
    median_days_since_scan: float = Field(..., description="Median scan age in days")
    never_scanned: int = Field(..., description="Servers with scan_count == 0")
    high_risk_count: int = Field(..., description="Servers with HIGH_RISK_ISOLATED or KNOWN_THREAT")

    model_config = {"from_attributes": True}


class ServerFreshnessEntry(BaseModel):
    server_id: str
    name: str
    last_scanned: Optional[str] = None
    last_assessed: Optional[str] = None
    days_since_scan: Optional[int] = None
    days_since_score: Optional[int] = None
    scan_status: str = Field(..., description="'fresh' | 'stale' | 'never'")
    score_status: str = Field(..., description="'fresh' | 'stale' | 'never'")
    overall_status: str = Field(..., description="'healthy' | 'degraded' | 'unknown'")


class DaemonFreshnessEntry(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: float
    status: str
    threshold_seconds: int


class FreshnessHealthResponse(BaseModel):
    total_servers: int
    fresh_servers: int
    stale_servers: int
    never_scored_servers: int
    servers: List[ServerFreshnessEntry]
    stale_daemons: List[DaemonFreshnessEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

HIGH_RISK_VERDICTS = {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}


def _scan_status(last_scanned: Optional[datetime]) -> str:
    if last_scanned is None:
        return "never"
    return "stale" if (datetime.utcnow() - last_scanned).days > SCAN_STALE_DAYS else "fresh"


def _score_status(last_assessed: Optional[datetime]) -> str:
    if last_assessed is None:
        return "never"
    return "stale" if (datetime.utcnow() - last_assessed).days > SCORE_STALE_DAYS else "fresh"


def _overall_status(scan_st: str, score_st: str) -> str:
    if scan_st == "stale" or score_st == "stale":
        return "degraded"
    if scan_st == "never" and score_st == "never":
        return "unknown"
    return "healthy"


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _query_service_health() -> List[dict]:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT service, status, last_heartbeat FROM service_health", "params": []},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"write_service unavailable: {exc}")
    rows = resp.json().get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=500, detail="malformed service_health response")
    return rows


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/registry/health-summary", response_model=HealthSummaryResponse)
def registry_health_summary(db: Session = Depends(get_session)) -> HealthSummaryResponse:
    """Aggregated registry health metrics."""
    now = datetime.now(timezone.utc)
    servers = db.query(McpServerRegistry).all()

    by_tier: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    trust_scores: list[float] = []
    scan_ages_days: list[float] = []
    never_scanned = 0
    high_risk_count = 0

    for s in servers:
        tier = s.risk_tier or "UNKNOWN"
        by_tier[tier] = by_tier.get(tier, 0) + 1
        verdict = s.verdict or "UNKNOWN"
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        if s.trust_score is not None:
            trust_scores.append(s.trust_score)
        if s.scan_count == 0:
            never_scanned += 1
        elif s.last_scanned:
            scan_ages_days.append((now - s.last_scanned).total_seconds() / 86400.0)
        if verdict in HIGH_RISK_VERDICTS:
            high_risk_count += 1

    return HealthSummaryResponse(
        total_servers=len(servers),
        by_tier=by_tier,
        by_verdict=by_verdict,
        avg_trust_score=round(sum(trust_scores) / len(trust_scores), 4) if trust_scores else 0.0,
        median_days_since_scan=round(median(scan_ages_days), 4) if scan_ages_days else 0.0,
        never_scanned=never_scanned,
        high_risk_count=high_risk_count,
    )


@router.get("/servers/freshness/health", response_model=FreshnessHealthResponse)
def servers_freshness_health(db: Session = Depends(get_session)) -> FreshnessHealthResponse:
    """Per-server freshness status with stale daemon list."""
    now = datetime.utcnow()

    latest = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_assessed"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.last_scanned,
            latest.c.last_assessed,
        )
        .outerjoin(latest, McpServerRegistry.server_id == latest.c.server_id)
        .all()
    )

    servers_entries: List[ServerFreshnessEntry] = []
    fresh_servers = stale_servers = never_scored_servers = 0

    for r in rows:
        ls = _scan_status(r.last_scanned)
        ss = _score_status(r.last_assessed)
        ov = _overall_status(ls, ss)
        days_scan = (now - r.last_scanned).days if r.last_scanned else None
        days_score = (now - r.last_assessed).days if r.last_assessed else None

        servers_entries.append(
            ServerFreshnessEntry(
                server_id=r.server_id,
                name=r.name or "",
                last_scanned=r.last_scanned.isoformat() if r.last_scanned else None,
                last_assessed=r.last_assessed.isoformat() if r.last_assessed else None,
                days_since_scan=days_scan,
                days_since_score=days_score,
                scan_status=ls,
                score_status=ss,
                overall_status=ov,
            )
        )

        if ov == "healthy":
            fresh_servers += 1
        elif ov == "degraded":
            stale_servers += 1
        else:
            never_scored_servers += 1

    stale_daemons: List[DaemonFreshnessEntry] = []
    try:
        health_rows = _query_service_health()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        for row in health_rows:
            name = row.get("service")
            raw_ts = row.get("last_heartbeat")
            if not name or not raw_ts:
                continue
            try:
                hb = _parse_utc(raw_ts)
            except Exception:
                continue
            age_seconds = (now_utc - hb).total_seconds()
            threshold = DAEMON_THRESHOLD_SECONDS.get(name, 300)
            if age_seconds > threshold:
                stale_daemons.append(
                    DaemonFreshnessEntry(
                        name=name,
                        last_heartbeat=raw_ts,
                        age_seconds=age_seconds,
                        status=row.get("status", "unknown"),
                        threshold_seconds=threshold,
                    )
                )
    except HTTPException:
        pass

    return FreshnessHealthResponse(
        total_servers=len(servers_entries),
        fresh_servers=fresh_servers,
        stale_servers=stale_servers,
        never_scored_servers=never_scored_servers,
        servers=servers_entries,
        stale_daemons=stale_daemons,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI

    # Build isolated test app
    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite via StaticPool (correct spelling: from sqlalchemy.pool)
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Seed data
    now = datetime.now(timezone.utc)
    seed_servers = [
        McpServerRegistry(
            server_id="srv-001", name="Fresh Server", risk_tier="LOW", verdict="CLEAN",
            trust_score=0.85, scan_count=1, last_scanned=now,
            registry_source="test", url="http://srv1",
        ),
        McpServerRegistry(
            server_id="srv-002", name="Stale Scan", risk_tier="MEDIUM", verdict="SUSPICIOUS",
            trust_score=0.55, scan_count=2,
            last_scanned=now - datetime.timedelta(days=8),
            registry_source="test", url="http://srv2",
        ),
        McpServerRegistry(
            server_id="srv-003", name="Never Scored", risk_tier="LOW", verdict="CLEAN",
            trust_score=0.90, scan_count=0, last_scanned=None,
            registry_source="test", url="http://srv3",
        ),
        McpServerRegistry(
            server_id="srv-004", name="All Stale", risk_tier="HIGH", verdict="HIGH_RISK_ISOLATED",
            trust_score=0.10, scan_count=3, last_scanned=now - datetime.timedelta(days=10),
            registry_source="test", url="http://srv4",
        ),
        McpServerRegistry(
            server_id="srv-005", name="Known Threat", risk_tier="HIGH", verdict="KNOWN_THREAT",
            trust_score=0.05, scan_count=5, last_scanned=now,
            registry_source="test", url="http://srv5",
        ),
    ]

    seed_scores = [
        # srv-001: fresh score
        McpLlmAxisScore(
            server_id="srv-001", axis_name="overall_risk", label="LOW", label_index=0,
            model_version="test", scored_at=now,
        ),
        # srv-002: fresh score (scan stale but score fresh)
        McpLlmAxisScore(
            server_id="srv-002", axis_name="overall_risk", label="MEDIUM", label_index=1,
            model_version="test", scored_at=now,
        ),
        # srv-004: stale score
        McpLlmAxisScore(
            server_id="srv-004", axis_name="overall_risk", label="HIGH", label_index=3,
            model_version="test", scored_at=now - datetime.timedelta(days=10),
        ),
        # srv-005: fresh score
        McpLlmAxisScore(
            server_id="srv-005", axis_name="overall_risk", label="CRITICAL", label_index=4,
            model_version="test", scored_at=now,
        ),
    ]

    with TestSession() as sess:
        for s in seed_servers:
            sess.add(s)
        for s in seed_scores:
            sess.add(s)
        sess.commit()

    def _override() -> Generator[Session, None, None]:
        with TestSession() as s:
            yield s

    # Stub write_service so daemon query doesn't fail
    import requests as _requests
    _orig_post = _requests.post

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rows": []}  # empty daemon list

    _requests.post = lambda *a, **kw: _FakeResp()

    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)

    try:
        # --- Test 1: health summary ---
        resp = client.get("/api/registry/health-summary")
        if resp.status_code != 200:
            print(f"FAIL: health-summary returned {resp.status_code}")
            sys.exit(1)
        data = resp.json()
        assert data["total_servers"] == 5, f"total_servers expected 5, got {data['total_servers']}"
        assert data["high_risk_count"] == 2, f"high_risk_count expected 2, got {data['high_risk_count']}"
        assert data["never_scanned"] == 1, f"never_scanned expected 1, got {data['never_scanned']}"
        assert "CLEAN" in data["by_verdict"], f"by_verdict missing CLEAN: {data['by_verdict']}"
        assert "LOW" in data["by_tier"], f"by_tier missing LOW: {data['by_tier']}"

        # --- Test 2: freshness health ---
        resp = client.get("/api/servers/freshness/health")
        if resp.status_code != 200:
            print(f"FAIL: freshness/health returned {resp.status_code}")
            sys.exit(1)
        data = resp.json()
        assert data["total_servers"] == 5, f"total_servers expected 5, got {data['total_servers']}"
        # srv-001: healthy; srv-002: degraded (scan stale); srv-003: unknown (never scored);
        # srv-004: degraded (scan stale); srv-005: healthy
        assert data["fresh_servers"] == 2, f"fresh_servers expected 2, got {data['fresh_servers']}"
        assert data["stale_servers"] == 2, f"stale_servers expected 2, got {data['stale_servers']}"
        assert data["never_scored_servers"] == 1, f"never_scored_servers expected 1, got {data['never_scored_servers']}"

        print("PASS")
    finally:
        _requests.post = _orig_post
