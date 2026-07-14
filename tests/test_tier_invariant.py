"""The tier invariant: a server may only wear a risk tier it EARNED.

CofC ruling 2026-07-14 (FATHER R1/R2). The bug this locks out:

`apply_risk_tier_backfill.py` used to propagate risk_tier by URL -- every
registry row sharing a scored row's URL inherited that row's tier. But a repo
URL is not a server identity. Measured against prod: of 11,623 duplicate-URL
groups, only 332 were true duplicates (same name); 11,291 were DISTINCT SERVERS
sharing one repo URL. `github.com/codespar/mcp-dev-latam` alone held 71 rows --
mcp-nubank (PIX/transfers), mcp-nupay (merchant checkout), mcp-nuvem-fiscal
(e-invoicing), mcp-omie (ERP). Different tools, different data sensitivity,
different egress. All wearing ONE sibling's tier.

That stamped 14,015 rows (17.4% of the registry) with a tier nobody computed for
them -- and in the data it was INDISTINGUISHABLE from an earned one.

A gap is honest ("we don't know"). This ASSERTED a score derived from a sibling.
We sell provenance or we sell nothing.

FATHER declined a `score_basis` provenance column on the grounds that it would
only ever read 'direct' -- a column encoding a bug we just deleted. The
requirement ("inherited rows must be distinguishable from earned ones") is
instead satisfied by making inherited rows NOT EXIST, and pinning that here.

An uncalled helper is not a gate (see #1467). A comment is not an invariant.
This test is the invariant.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import McpLlmAxisScore, McpServerRegistry

UNASSESSED = "unassessed"


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _tier_invariant_violations(db):
    """Rows holding a real tier with NO direct axis score. Must always be []."""
    scored = select(McpLlmAxisScore.server_id).distinct().scalar_subquery()
    return db.scalars(
        select(McpServerRegistry.server_id)
        .where(McpServerRegistry.risk_tier.is_not(None))
        .where(McpServerRegistry.risk_tier != UNASSESSED)
        .where(McpServerRegistry.server_id.not_in(scored))
    ).all()


def test_earned_tier_is_allowed(db):
    db.add(McpServerRegistry(server_id="earned", name="a", url="https://x/repo",
                             risk_tier="HIGH_RISK_ISOLATED"))
    db.add(McpLlmAxisScore(id=1, server_id="earned", axis_name="overall_risk",
                           model_version="v3.0", scored_at=datetime.utcnow()))
    db.commit()
    assert _tier_invariant_violations(db) == []


def test_sibling_in_same_repo_may_not_inherit_a_tier(db):
    """THE regression. Two distinct servers, ONE repo URL. Only one is scored.

    The unscored sibling must NOT wear the scored one's tier -- it must read
    'unassessed'. This is the codespar/mcp-dev-latam case in miniature.
    """
    url = "https://github.com/codespar/mcp-dev-latam"
    db.add(McpServerRegistry(server_id="s1", name="io.github.codespar/mcp-nubank",
                             url=url, risk_tier="TRUSTED_GENERAL"))
    db.add(McpLlmAxisScore(id=1, server_id="s1", axis_name="overall_risk",
                           model_version="v3.0", scored_at=datetime.utcnow()))
    # the sibling: same repo, different product, NEVER scored
    db.add(McpServerRegistry(server_id="s2", name="io.github.codespar/mcp-nupay",
                             url=url, risk_tier=UNASSESSED))
    db.commit()
    assert _tier_invariant_violations(db) == []


def test_inherited_tier_is_a_violation(db):
    """If propagation is ever reintroduced, this test fails. That is its job."""
    url = "https://github.com/codespar/mcp-dev-latam"
    db.add(McpServerRegistry(server_id="s1", name="mcp-nubank", url=url,
                             risk_tier="TRUSTED_GENERAL"))
    db.add(McpLlmAxisScore(id=1, server_id="s1", axis_name="overall_risk",
                           model_version="v3.0", scored_at=datetime.utcnow()))
    # s2 wears s1's tier without ever having been scored -- fabricated provenance
    db.add(McpServerRegistry(server_id="s2", name="mcp-nupay", url=url,
                             risk_tier="TRUSTED_GENERAL"))
    db.commit()
    assert _tier_invariant_violations(db) == ["s2"], (
        "a row wearing a tier it never earned MUST be flagged")


def test_never_scored_reads_unassessed_not_null(db):
    """'unassessed', not NULL: it is the discovery default and every consumer
    (ask_retrieval_service, fleet_risk_tier_trend_api, the promoters) handles it.
    NULL would break joins that a revert must not break."""
    db.add(McpServerRegistry(server_id="cold", name="n", url="https://y/repo",
                             risk_tier=UNASSESSED))
    db.commit()
    assert _tier_invariant_violations(db) == []
    row = db.get(McpServerRegistry, "cold")
    assert row.risk_tier == UNASSESSED and row.risk_tier is not None


def test_propagation_sql_is_not_in_the_backfill_scripts():
    """Belt and braces: the URL-join UPDATE must not come back by copy-paste.
    There are TWO copies of this script; both must stay clean."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("tools/apply_risk_tier_backfill.py",
                "tools/rescore/apply_risk_tier_backfill.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "r.url = x.url" not in src, (
            f"{rel} reintroduced URL tier-propagation -- a repo URL is not a "
            f"server identity (CofC 2026-07-14)")
