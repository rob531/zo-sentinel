"""THE LINE, tested. CofC ruling 2026-07-14.

The bug these tests exist to prevent: an 11-day-old score reported "FRESH"
because the gate's SLA (30d) had silently drifted from the operational SLA (7d),
and because nothing ever called the gate.
"""
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import McpLlmAxisScore
import freshness_gate as fg
from freshness_gate import SurfaceClass


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime.utcnow()
    s.add_all([
        McpLlmAxisScore(id=1, server_id="fresh1", axis_name="overall_risk",
                        model_version="v3.0", scored_at=now - timedelta(days=2)),
        # the exact shape of the 2026-07-14 incident: 11 days old, 7-day SLA
        McpLlmAxisScore(id=2, server_id="moat11", axis_name="overall_risk",
                        model_version="v3.0", scored_at=now - timedelta(days=11)),
        McpLlmAxisScore(id=3, server_id="ancient", axis_name="overall_risk",
                        model_version="v2.1", scored_at=now - timedelta(days=45)),
    ])
    s.commit()
    yield s
    s.close()


def test_default_sla_is_seven_days_not_thirty():
    os.environ.pop("FRESHNESS_SLA_DAYS", None)
    assert fg.sla_days() == 7, "the 30d default is what made 11d data look FRESH"


def test_eleven_day_old_score_is_not_fresh(db):
    """The regression. Under the old 30d default this returned FRESH."""
    os.environ.pop("FRESHNESS_SLA_DAYS", None)
    env = fg.freshness_envelope(db, "moat11")
    assert env["status"] == "aging"          # > 7d SLA
    assert env["status"] != "fresh"
    assert 10.5 < env["age_days"] < 11.5
    assert fg.is_fresh(db, "moat11") is False


def test_never_scored_is_not_treated_as_fine(db):
    env = fg.freshness_envelope(db, "does-not-exist")
    assert env["status"] == "never_scored"
    assert env["age_days"] is None           # None is NOT zero, NOT fresh
    assert fg.is_fresh(db, "does-not-exist") is False


def test_keyed_surface_fails_closed_on_stale(db):
    with pytest.raises(HTTPException) as e:
        fg.assert_fresh(db, "ancient", SurfaceClass.KEYED)
    assert e.value.status_code == 503
    assert e.value.detail["error"] == "stale_data"
    assert e.value.detail["age_days"] > 7


def test_keyed_surface_fails_closed_on_never_scored(db):
    with pytest.raises(HTTPException) as e:
        fg.assert_fresh(db, "nope", SurfaceClass.KEYED)
    assert e.value.status_code == 503


def test_keyed_surface_serves_when_fresh(db):
    env = fg.assert_fresh(db, "fresh1", SurfaceClass.KEYED)
    assert env["status"] == "fresh"


def test_public_surface_never_fails_closed(db):
    """A gate that 503s a live public read is worse than the staleness."""
    for sid in ("fresh1", "moat11", "ancient", "never-seen"):
        env = fg.assert_fresh(db, sid, SurfaceClass.PUBLIC)   # must not raise
        assert "status" in env and "age_days" in env


def test_fleet_freshness_reports_breach(db):
    os.environ.pop("FRESHNESS_SLA_DAYS", None)
    fleet = fg.fleet_freshness(db)
    assert fleet["sla_days"] == 7
    assert fleet["corpus_age_days"] < 7        # newest row is 2d old
    assert fleet["breaching_sla"] is False


def test_sla_is_env_tunable_without_deploy(db):
    os.environ["FRESHNESS_SLA_DAYS"] = "30"
    try:
        assert fg.freshness_envelope(db, "moat11")["status"] == "fresh"
    finally:
        os.environ.pop("FRESHNESS_SLA_DAYS", None)


def test_metadata_api_shares_the_one_sla_number():
    """freshness_metadata_api must not re-introduce a second SLA constant."""
    import freshness_metadata_api as fma
    os.environ.pop("FRESHNESS_SLA_DAYS", None)
    assert fma.sla_days() == fg.sla_days() == 7
