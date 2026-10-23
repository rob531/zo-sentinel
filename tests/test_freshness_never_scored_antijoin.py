"""Two-pole test for the `/freshness` never_scored coverage count.

DEFECT (GC-8 echo / GC-5 silent mutation): scoring_freshness_surface._compute
reported the coverage hole as an ARITHMETIC PROXY

    never_scored = registry_rows - scored_servers
                 = COUNT(*) FROM mcp_server_registry
                   - COUNT(DISTINCT server_id) FROM mcp_llm_axis_scores

That proxy only equals the true count ("registry servers with NO score") when
*every* scored server_id is also a registry row. It is NOT: McpLlmAxisScore.
server_id carries no foreign key to McpServerRegistry (app/models.py), and the
score table is append-only while the registry is re-promoted under fresh
server_ids (auto_promoter.py uses gen_random_uuid per promotion). So an ORPHAN
score -- a server_id scored but absent from the registry -- silently credits
the subtraction as though it covered a registry server, and the published
coverage gap drifts away from the truth.

The sibling endpoint `/servers/never-scored` (never_scored_backlog_api.py)
already computes the CORRECT set difference (registry WHERE server_id NOT IN
scores). This test pins /freshness to that same, already-shipped definition.

RED on the proxy: with 3 registry servers {A,B,C}, a real score for A and an
ORPHAN score for X (not in the registry):
    registry_rows = 3, scored_servers (distinct) = {A, X} = 2
    proxy never_scored = max(0, 3 - 2) = 1          # WRONG
    truth (anti-join)  = {B, C}                 = 2  # registry with no score
GREEN on the anti-join: never_scored == 2.

Hermetic: in-memory sqlite, no network. Mirrors tests/test_freshness_metadata.py.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import McpLlmAxisScore, McpServerRegistry
import scoring_freshness_surface


def _seeded_session():
    """3 registry servers; one real score (A) + one ORPHAN score (X)."""
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime(2026, 10, 6, 8, 51, 46)
    for sid in ("A", "B", "C"):
        s.add(McpServerRegistry(server_id=sid, name=f"srv-{sid}",
                                registry_source="test"))
    # A is a registry server WITH a score.
    s.add(McpLlmAxisScore(id=1, server_id="A", axis_name="auth_strength",
                          model_version="v1", scored_at=now))
    # X is scored but has NO registry row -> an orphan score. The proxy counts
    # it toward scored_servers and wrongly shrinks the coverage gap.
    s.add(McpLlmAxisScore(id=2, server_id="X", axis_name="auth_strength",
                          model_version="v1", scored_at=now))
    s.commit()
    return s


def test_never_scored_counts_registry_not_scored_not_rows_minus_servers():
    s = _seeded_session()
    try:
        out = scoring_freshness_surface._compute(s)
    finally:
        s.close()

    # Inputs that make the proxy and the truth diverge (documents the defect).
    assert out["registry_rows"] == 3, out
    assert out["scored_servers"] == 2, out  # {A, X}; X is the orphan

    # The proxy (registry_rows - scored_servers) would give 1 here. The true
    # count of registry servers with no score is {B, C} = 2.
    assert out["never_scored"] == 2, (
        f"never_scored={out['never_scored']} -- the rows-minus-servers proxy "
        f"(3-2=1) miscounts; the anti-join truth is 2 (B, C). {out}"
    )


def test_never_scored_zero_when_all_registry_servers_scored():
    """No coverage gap when every registry server has a score (orphans aside)."""
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime(2026, 10, 6, 8, 51, 46)
    for i, sid in enumerate(("A", "B"), start=1):
        s.add(McpServerRegistry(server_id=sid, name=sid, registry_source="t"))
        s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name="auth_strength",
                              model_version="v1", scored_at=now))
    s.add(McpLlmAxisScore(id=99, server_id="ORPHAN", axis_name="auth_strength",
                          model_version="v1", scored_at=now))
    s.commit()
    try:
        out = scoring_freshness_surface._compute(s)
    finally:
        s.close()
    assert out["never_scored"] == 0, out


if __name__ == "__main__":
    test_never_scored_counts_registry_not_scored_not_rows_minus_servers()
    test_never_scored_zero_when_all_registry_servers_scored()
    print("PASS")
