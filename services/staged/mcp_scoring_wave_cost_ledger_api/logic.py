import datetime
from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import CadenceJobRun


class WaveEntry(BaseModel):
    job: str
    started_at: datetime.datetime
    finished_at: Optional[datetime.datetime] = None
    rows_affected: int
    duration_seconds: Optional[float] = None
    detail: Optional[str] = None

    class Config:
        orm_mode = True


class WaveLedgerResponse(BaseModel):
    waves: List[WaveEntry] = Field(default_factory=list)


def get_wave_cost_ledger(session: Session = Depends(get_session)) -> WaveLedgerResponse:
    """Return recent cadence job runs whose job name starts with ``scoring`` or ``score``."""
    runs = (
        session.query(CadenceJobRun)
        .filter(
            or_(
                CadenceJobRun.job.ilike("scoring%"),
                CadenceJobRun.job.ilike("score%"),
            )
        )
        .order_by(CadenceJobRun.started_at.desc())
        .all()
    )

    wave_list: List[WaveEntry] = []
    for run in runs:
        duration = None
        if run.started_at and run.finished_at:
            duration = (run.finished_at - run.started_at).total_seconds()
        wave_list.append(
            WaveEntry(
                job=run.job,
                started_at=run.started_at,
                finished_at=run.finished_at,
                rows_affected=run.rows_affected or 0,
                duration_seconds=duration,
                detail=run.detail,
            )
        )
    return WaveLedgerResponse(waves=wave_list)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the FastAPI dependency with a test session provider
    def get_test_session() -> Session:
        with TestSession() as s:
            yield s

    # Seed three mock CadenceJobRun rows
    now = datetime.datetime.utcnow()
    mock_runs = [
        CadenceJobRun(
            job="scoring_wave_1",
            started_at=now - datetime.timedelta(minutes=30),
            finished_at=now - datetime.timedelta(minutes=20),
            rows_affected=10,
            detail="first wave",
            status="completed",
        ),
        CadenceJobRun(
            job="score_wave_2",
            started_at=now - datetime.timedelta(minutes=20),
            finished_at=now - datetime.timedelta(minutes=10),
            rows_affected=5,
            detail="second wave",
            status="completed",
        ),
        CadenceJobRun(
            job="scoring_wave_3",
            started_at=now - datetime.timedelta(minutes=10),
            finished_at=now,
            rows_affected=0,
            detail="third wave",
            status="completed",
        ),
    ]

    with TestSession() as sess:
        sess.add_all(mock_runs)
        sess.commit()

    # Run the logic using the test session
    response = get_wave_cost_ledger(session=next(get_test_session()))
    assert isinstance(response, WaveLedgerResponse)
    assert len(response.waves) >= 3
    for w in response.waves:
        assert w.rows_affected >= 0

    print("PASS")