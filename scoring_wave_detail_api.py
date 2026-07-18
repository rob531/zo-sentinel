"""scoring_wave_detail_api.py -- read-only view of a single scoring wave run.

GET /scoring/waves/{wave_id}          -> wave metadata from cadence_job_runs
GET /scoring/waves/{wave_id}/servers  -> server_ids scored in that wave

Mounted by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, McpLlmAxisScore

router = APIRouter(prefix="/scoring", tags=["scoring"])


class WaveMetadata(BaseModel):
    wave_id: int
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None
    detail: Optional[dict] = None


class WaveServers(BaseModel):
    wave_id: int
    server_ids: list[str]
    count: int


def _job_run_to_wave_id(job: str) -> Optional[int]:
    """Extract numeric wave_id from a job name like 'scoring_wave_7'."""
    if job.startswith("scoring_wave_"):
        try:
            return int(job[len("scoring_wave_"):])
        except ValueError:
            pass
    return None


@router.get("/waves/{wave_id}", response_model=WaveMetadata)
def get_wave(wave_id: int, db: Session = Depends(get_session)) -> WaveMetadata:
    """Return the most recent run for scoring_wave_{wave_id}."""
    job_name = f"scoring_wave_{wave_id}"
    row = (
        db.execute(
            select(CadenceJobRun)
            .where(CadenceJobRun.job == job_name)
            .order_by(CadenceJobRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No wave run found for wave_id {wave_id}")
    return WaveMetadata(
        wave_id=wave_id,
        job=row.job,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        rows_affected=row.rows_affected,
        detail=row.detail,
    )


@router.get("/waves/{wave_id}/servers", response_model=WaveServers)
def get_wave_servers(wave_id: int, db: Session = Depends(get_session)) -> WaveServers:
    """Return the list of server_ids scored in the most recent run of scoring_wave_{wave_id}."""
    job_name = f"scoring_wave_{wave_id}"
    row = (
        db.execute(
            select(CadenceJobRun)
            .where(CadenceJobRun.job == job_name)
            .order_by(CadenceJobRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No wave run found for wave_id {wave_id}")

    # Find server_ids scored in this wave via mcp_llm_axis_scores for the
    # model_version that was active when this wave ran.
    model_version = None
    if row.finished_at:
        mv_row = (
            db.execute(
                select(McpLlmAxisScore.model_version)
                .where(McpLlmAxisScore.scored_at <= row.finished_at)
                .order_by(McpLlmAxisScore.scored_at.desc())
                .limit(1)
            )
            .scalar_one_or_none()
        )
        model_version = mv_row
    else:
        # Wave still running or no scored_at reference; use latest model_version.
        mv_row = (
            db.execute(
                select(McpLlmAxisScore.model_version)
                .order_by(McpLlmAxisScore.scored_at.desc())
                .limit(1)
            )
            .scalar_one_or_none()
        )
        model_version = mv_row

    if model_version is None:
        return WaveServers(wave_id=wave_id, server_ids=[], count=0)

    server_ids = [
        r[0]
        for r in db.execute(
            select(McpLlmAxisScore.server_id)
            .where(McpLlmAxisScore.model_version == model_version)
            .distinct()
        ).all()
    ]

    return WaveServers(wave_id=wave_id, server_ids=server_ids, count=len(server_ids))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed two wave runs (no McpLlmAxisScore rows -> /servers returns empty)
    s = TS()
    now = datetime(2026, 7, 10, 12, 0, 0)
    later = datetime(2026, 7, 10, 13, 0, 0)
    s.add(
        CadenceJobRun(
            id=1,
            job="scoring_wave_1",
            status="ok",
            started_at=now,
            finished_at=later,
            rows_affected=150,
            detail={"wave_id": 1, "batch": 1},
        )
    )
    s.add(
        CadenceJobRun(
            id=2,
            job="scoring_wave_2",
            status="failed",
            started_at=now,
            finished_at=later,
            rows_affected=None,
            detail={"wave_id": 2, "error": "timeout"},
        )
    )
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # GET /scoring/waves/1 returns wave metadata
    r = c.get("/scoring/waves/1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["wave_id"] == 1, j
    assert j["job"] == "scoring_wave_1", j
    assert j["status"] == "ok", j
    assert j["rows_affected"] == 150, j

    # GET /scoring/waves/1/servers returns empty list (no axis scores in test)
    r = c.get("/scoring/waves/1/servers")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["wave_id"] == 1, j
    assert j["server_ids"] == [], j
    assert j["count"] == 0, j

    # 404 on unknown wave
    r = c.get("/scoring/waves/99")
    assert r.status_code == 404, r.text

    r = c.get("/scoring/waves/99/servers")
    assert r.status_code == 404, r.text

    print("PASS")
