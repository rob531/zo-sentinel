import json
from datetime import datetime
from typing import List

from fastapi import Depends
from pydantic import BaseModel

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun  # type: ignore


class WaveCost(BaseModel):
    wave_id: int
    job_name: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    rows_scored: int
    estimated_cost_units: float


class WaveCostLedgerResponse(BaseModel):
    waves: List[WaveCost]


def _fetch_wave_costs(session: Session) -> List[WaveCost]:
    stmt = select(CadenceJobRun).where(
        or_(
            CadenceJobRun.job.like("scoring_wave%"),
            CadenceJobRun.job.like("score_run%"),
        )
    )
    records = session.execute(stmt).scalars().all()

    result: List[WaveCost] = []
    for rec in records:
        # compute duration
        if rec.started_at and rec.finished_at:
            duration = (rec.finished_at - rec.started_at).total_seconds()
        else:
            duration = 0.0

        # rows_scored from JSON detail if present, else fallback to rows_affected
        try:
            detail_dict = json.loads(rec.detail) if isinstance(rec.detail, str) else rec.detail or {}
        except Exception:
            detail_dict = {}
        rows_scored = int(detail_dict.get("rows_scored", rec.rows_affected or 0))

        # estimated cost units = rows_affected * duration_seconds
        rows_affected = rec.rows_affected or 0
        estimated_cost = rows_affected * duration

        result.append(
            WaveCost(
                wave_id=rec.id,
                job_name=rec.job,
                started_at=rec.started_at,
                finished_at=rec.finished_at,
                duration_seconds=duration,
                rows_scored=rows_scored,
                estimated_cost_units=estimated_cost,
            )
        )
    return result


def get_scoring_wave_costs(session: Session = Depends(get_session)) -> WaveCostLedgerResponse:
    waves = _fetch_wave_costs(session)
    return WaveCostLedgerResponse(waves=waves)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import Base to create tables – adjust if your project uses a different name
    from app.db import Base  # type: ignore

    # In‑memory SQLite for isolated test
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    now = datetime.utcnow()
    with SessionLocal() as test_session:
        # two scoring waves that should be returned
        test_session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="scoring_wave_1",
                    status="completed",
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    rows_affected=100,
                    detail=json.dumps({"rows_scored": 1000}),
                ),
                CadenceJobRun(
                    id=2,
                    job="score_run_2",
                    status="completed",
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    rows_affected=100,
                    detail=json.dumps({"rows_scored": 2000}),
                ),
                # non‑matching job – should be ignored
                CadenceJobRun(
                    id=3,
                    job="other_job",
                    status="completed",
                    started_at=now - timedelta(seconds=1),
                    finished_at=now,
                    rows_affected=50,
                    detail=json.dumps({}),
                ),
            ]
        )
        test_session.commit()

        # invoke the core logic directly
        waves = _fetch_wave_costs(test_session)

        # assertions per acceptance criteria
        total_estimated = sum(w.estimated_cost_units for w in waves)
        assert total_estimated == 200, f"expected total cost 200, got {total_estimated}"
        assert len(waves) == 2, f"expected 2 waves, got {len(waves)}"
        for w in waves:
            assert w.rows_scored > 0, "rows_scored should be non‑zero"

        print("PASS")