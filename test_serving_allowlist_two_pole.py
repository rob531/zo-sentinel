"""test_serving_allowlist_two_pole.py -- two-pole guard for the serving-read hazard.

Hermetic (in-memory SQLite, no network, no :8772, no `from app import app`), so it runs
anywhere the ORM imports. It exercises the LIVE mounted serving function
axis_scores_query_api.get_server_axis_summary directly.

THE HAZARD: that function serves one row per axis by NEWEST scored_at and honored no
allowlist, so a partial/experimental write with a fresh timestamp hijacked the served
product (POSTMORTEM_20261030: 1541 servers degraded). The fix adds a flag-gated serving
allowlist (serving_model_allowlist.py; env SERVING_FULL_MODEL_ALLOWLIST), default OFF.

TWO POLES (and the fail control):
  POLE A (flag OFF, default): the newer partial off-allowlist row IS served -- i.e. the
          prior newest-scored_at-per-axis behavior is preserved byte-for-byte.
  POLE B (flag ON, armed to the full model only): the newer off-allowlist row is IGNORED;
          the armed full model's row is served.
  FAIL CONTROL: POLE B's served value MUST DIFFER from POLE A's. If the allowlist did
          nothing (the bug), both poles would return the same hijacked value and the
          `_on != _off` assertion would FAIL -- so the test is discriminating, not
          a tautology.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import McpLlmAxisScore
from axis_scores_query_api import get_server_axis_summary


def _served_overall_risk(db) -> str | None:
    resp = asyncio.new_event_loop().run_until_complete(
        get_server_axis_summary("srvX", db))
    return {r.axis_name: r.label for r in resp.rows}.get("overall_risk")


def main() -> None:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    McpLlmAxisScore.__table__.create(eng)
    db = sessionmaker(bind=eng, autoflush=False, autocommit=False)()

    t0 = datetime(2026, 1, 1, 0, 0, 0)
    # Armed FULL model: GOOD row on overall_risk (older). Explicit id: the model's id is
    # BigInteger, which SQLite does not autoincrement (only INTEGER PRIMARY KEY).
    db.add(McpLlmAxisScore(id=1, server_id="srvX", axis_name="overall_risk", label="GOOD",
                           model_version="v5_prod", scored_at=t0))
    # Partial live probe: a NEWER row on the SAME axis under an UN-armed model_version.
    # Under newest-scored_at this WINS -- exactly the hijack the incident hit.
    db.add(McpLlmAxisScore(id=2, server_id="srvX", axis_name="overall_risk", label="HIJACK",
                           model_version="lpg_probe_v1", scored_at=t0 + timedelta(hours=1)))
    db.commit()

    # POLE A -- OFF (default): preserve today's newest-scored_at behavior -> HIJACK served.
    os.environ.pop("SERVING_FULL_MODEL_ALLOWLIST", None)
    off = _served_overall_risk(db)
    assert off == "HIJACK", f"OFF must preserve newest-scored_at behavior, got {off!r}"

    # POLE B -- ON (armed to the full model only): off-allowlist newer row IGNORED.
    os.environ["SERVING_FULL_MODEL_ALLOWLIST"] = "v5_prod"
    on = _served_overall_risk(db)
    assert on == "GOOD", f"ON must serve the armed full model, not the hijack, got {on!r}"

    # FAIL CONTROL -- the poles must differ, or the allowlist did nothing.
    assert on != off, "two-pole not discriminating: ON == OFF"

    # Belt-and-braces: an allowlist that excludes BOTH models serves nothing for that axis
    # (no silent fallback to newest), proving the filter is really applied.
    os.environ["SERVING_FULL_MODEL_ALLOWLIST"] = "v9_nonexistent"
    none_served = _served_overall_risk(db)
    assert none_served is None, f"allowlist excluding all models must serve nothing, got {none_served!r}"

    os.environ.pop("SERVING_FULL_MODEL_ALLOWLIST", None)
    db.close()
    print("PASS")


if __name__ == "__main__":
    main()
