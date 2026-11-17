"""0016_registry_facet_indexes must create the three registry facet indexes.

The dashboard-summary and facet GROUP BYs on mcp_server_registry
(risk_tier / verdict) and the min/max(trust_score) scan are full seq scans
without these btrees (root cause of the ~74s /api/dashboard/summary and the slow
cold /api/facets, 2026-11-11). This test applies the migration's `upgrade()`
against a sqlite build of the table and asserts the indexes land on the right
columns and that `downgrade()` removes them -- so a refactor that drops an index
name or points it at the wrong column fails CI, not prod.

sqlite is the CI/dev dialect (app/db.py); the migration's postgres-only
ownership guard is skipped there, exercising the plain IF NOT EXISTS path.
"""
from __future__ import annotations

import importlib.util
import os

from sqlalchemy import create_engine, inspect

from app.db import Base
from app import models  # noqa: F401  -- register mappers so create_all builds the table

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(
    REPO_ROOT, "migrations", "versions", "0016_registry_facet_indexes.py"
)

EXPECTED = {
    "ix_registry_risk_tier": "risk_tier",
    "ix_registry_verdict": "verdict",
    "ix_registry_trust_score": "trust_score",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("m0016", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _indexes_on_registry(engine):
    insp = inspect(engine)
    return {
        ix["name"]: list(ix["column_names"])
        for ix in insp.get_indexes("mcp_server_registry")
    }


def _run(engine, fn):
    """op.get_bind()/op.execute need an alembic MigrationContext bound to a
    connection; stand one up around a live transaction. Operations.context()
    installs the module-level `alembic.op` proxy the migration calls through."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()


def test_upgrade_creates_the_three_registry_indexes(tmp_path):
    db_path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    mod = _load_migration()

    _run(engine, mod.upgrade)
    idx = _indexes_on_registry(engine)
    for name, col in EXPECTED.items():
        assert name in idx, f"{name} not created by upgrade()"
        assert idx[name] == [col], f"{name} should index [{col}], got {idx[name]}"

    _run(engine, mod.downgrade)
    idx_after = _indexes_on_registry(engine)
    for name in EXPECTED:
        assert name not in idx_after, f"{name} survived downgrade()"


def test_upgrade_is_idempotent(tmp_path):
    """Re-running upgrade() (IF NOT EXISTS) must not error -- mirrors a
    re-applied release or a prod index already pre-created CONCURRENTLY."""
    db_path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    mod = _load_migration()

    _run(engine, mod.upgrade)
    _run(engine, mod.upgrade)  # second pass: must be a no-op, not a failure
    idx = _indexes_on_registry(engine)
    assert set(EXPECTED).issubset(idx)
