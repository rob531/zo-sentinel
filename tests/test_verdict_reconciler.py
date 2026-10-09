"""Two-pole gate for the RC-1 verdict reconciler (hermetic sqlite, no network).

Seeds exactly three servers:
  A  srv_unknown_scored  -- verdict='unknown' + 7 axis scores  -> candidate
  B  srv_verdicted       -- verdict='TRUSTED_RESEARCH' + 7 axes -> NOT a candidate
  C  srv_no_scores       -- verdict='unknown', NO axis scores   -> NOT a candidate

RED  (pre-fix):  without reconciliation the scored-but-unknown row A stays
                 'unknown' -- this is RC-1 (axis scores land, nothing re-verdicts).
GREEN (post-fix): the dry-run counter reports EXACTLY 1 would-flip, and applying
                 the reconciler flips A to its axis-derived verdict while B and C
                 are untouched.

The reconciler REUSES the canonical mapping scoring_tier_persister._compute_tier;
this test does not redefine any taxonomy or threshold.
"""
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base, McpLlmAxisScore, McpServerRegistry  # noqa: E402
import verdict_reconciler as vr  # noqa: E402

AXES = (
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
)
MODEL_VERSION = "v3.0_40974559"

SRV_A = "srv_unknown_scored"   # unknown + scores  -> should reconcile
SRV_B = "srv_verdicted"        # already verdicted -> untouched
SRV_C = "srv_no_scores"        # unknown, no scores -> untouched


def _make_session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False)()


def _seed(session):
    # A: verdict='unknown' + all 7 axes p_top=0.6 -> canonical mapping Rule 2
    #    (all axes present, majority p_top>0.5) -> TRUSTED_GENERAL.
    session.add(McpServerRegistry(server_id=SRV_A, name="Scored Unknown MCP",
                                  url="https://github.com/stripe/agent-toolkit",
                                  verdict=vr.UNKNOWN_VERDICT))
    for i, ax in enumerate(AXES, start=1):
        session.add(McpLlmAxisScore(id=i, server_id=SRV_A, axis_name=ax,
                                    label="LOW", p_top=0.6,
                                    model_version=MODEL_VERSION))

    # B: already carries a real verdict (and also has axis scores) -> excluded
    #    from the candidate set because verdict != 'unknown'.
    session.add(McpServerRegistry(server_id=SRV_B, name="Verdicted MCP",
                                  url="https://example.com/verdicted",
                                  verdict="TRUSTED_RESEARCH"))
    base = len(AXES) + 1
    for i, ax in enumerate(AXES, start=base):
        session.add(McpLlmAxisScore(id=i, server_id=SRV_B, axis_name=ax,
                                    label="LOW", p_top=0.6,
                                    model_version=MODEL_VERSION))

    # C: verdict='unknown' but NO axis scores -> excluded by the ">=1 axis row"
    #    guard (proves the guard, not just the verdict filter).
    session.add(McpServerRegistry(server_id=SRV_C, name="No Scores MCP",
                                  url="https://example.com/noscores",
                                  verdict=vr.UNKNOWN_VERDICT))
    session.commit()


def _verdict(session, sid):
    return session.execute(
        select(McpServerRegistry.verdict).where(McpServerRegistry.server_id == sid)
    ).scalar_one()


def run():
    session = _make_session()
    _seed(session)

    # ---- RED (pre-fix): nothing has re-verdicted A; it is still 'unknown'. ----
    assert _verdict(session, SRV_A) == vr.UNKNOWN_VERDICT, "RED precondition: A unknown"
    assert _verdict(session, SRV_B) == "TRUSTED_RESEARCH"
    assert _verdict(session, SRV_C) == vr.UNKNOWN_VERDICT

    # ---- DRY RUN: exactly 1 would-flip, to the axis-derived verdict. ----
    report = vr.dry_run(session)
    assert report.candidates_considered == 1, (
        f"expected 1 candidate (only A), got {report.candidates_considered}")
    assert report.would_flip == 1, f"expected exactly 1 would-flip, got {report.would_flip}"
    assert report.flip_distribution == {"TRUSTED_GENERAL": 1}, report.flip_distribution

    # dry_run is READ-ONLY: verdicts unchanged.
    assert _verdict(session, SRV_A) == vr.UNKNOWN_VERDICT, "dry_run must not write"
    assert _verdict(session, SRV_B) == "TRUSTED_RESEARCH"
    assert _verdict(session, SRV_C) == vr.UNKNOWN_VERDICT

    # ---- GREEN (post-fix): apply via a session writer (hermetic, no network). ----
    result = vr.reconcile(session, writer=vr.session_writer(session), apply=True)
    assert result.applied == 1, f"expected 1 applied, got {result.applied}"

    # A reconciled to its axis-derived verdict; B and C untouched.
    assert _verdict(session, SRV_A) == "TRUSTED_GENERAL", "A should reconcile"
    assert _verdict(session, SRV_A) != vr.UNKNOWN_VERDICT, "A must no longer be unknown"
    assert _verdict(session, SRV_B) == "TRUSTED_RESEARCH", "B must be untouched"
    assert _verdict(session, SRV_C) == vr.UNKNOWN_VERDICT, "C (no scores) untouched"

    # Idempotent: a second dry-run now finds nothing to flip.
    assert vr.dry_run(session).would_flip == 0, "second pass should be a no-op"

    session.close()
    print("PASS")


if __name__ == "__main__":
    run()
