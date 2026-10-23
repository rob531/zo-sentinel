"""freshness_stats: one precomputed row per freshness surface (FU-667)

The public freshness routes (/freshness, /registry/freshness,
/registry/freshness-report) aggregate over the whole corpus (4.7M score rows /
560k registry rows on a 1-vCPU Fly PG). A cold compute is ~460s -- impossible
inside any request budget, and a per-request/per-worker background compute
multiplies that heavy query across gunicorn workers and saturates the single
small PG (the v122 pile-up that forced the v122->v121 rollback).

This table decouples compute from serve: ONE elected worker (guarded by a
Postgres advisory lock) recomputes each surface on a cadence and writes its
payload here with a computed_at; every worker and every route then reads a
single indexed row O(1). The heavy aggregate runs once per cadence, never
per-request, never once-per-worker.

Additive and reversible: a new table, no change to any existing column. Grades
EXPAND-ONLY / GREEN. On prod the release_command (alembic upgrade head) creates
it; it is tiny (one row per surface).
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_freshness_stats"
down_revision = "0014_axis_scored_at_index"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "freshness_stats",
        # surface key: 'scoring_freshness' | 'registry_freshness' |
        # 'registry_source_freshness_report'
        sa.Column("surface", sa.String(64), primary_key=True),
        # the full JSON payload the route serves (counts, buckets, timestamps)
        sa.Column("payload", sa.JSON, nullable=False),
        # when the aggregate that produced `payload` was computed (UTC)
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # how long that compute took, for observability
        sa.Column("compute_seconds", sa.Float, nullable=True),
    )
    # GRANT the app role write access. release_command runs as the OWNER role
    # ($OWNER_DATABASE_URL / mcplookup), so CREATE TABLE leaves the owner the
    # only role with privileges -- but the app serves and the snapshot refresher
    # WRITES as the app role (mcplookup_app), which would otherwise get
    # `permission denied for table freshness_stats` on INSERT (FU-667, caught in
    # the prod warm: the compute succeeded, the write was refused, so the surface
    # stayed 503). Mirror the grants mcp_server_registry already carries. Postgres
    # only; sqlite (CI/dev) has no role split, so guard on dialect.
    # GUARD on the role EXISTING: the prod moat PG has mcplookup_app, but the
    # CI/deploy-compat rehearsal Postgres does NOT -- a bare GRANT there raises
    # `role "mcplookup_app" does not exist` and fails the in-image alembic run
    # (caught by deploy-compat: boot_proof NOT PROVEN). A DO block that grants
    # only when the role is present works on prod and is a clean no-op elsewhere.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcplookup_app') THEN "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON freshness_stats TO mcplookup_app; "
            "END IF; END $$;"
        )


def downgrade():
    op.drop_table("freshness_stats")
