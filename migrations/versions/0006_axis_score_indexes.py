"""covering indexes for conditional facet counts (perspectives v1.2)

Revision ID: 0006_axis_score_indexes
Revises: 0005_vuln_intel
Create Date: 2026-07-03

Ops note (council ruling 2026-07-03): plain CREATE INDEX takes a lock and
CONCURRENTLY cannot run inside alembic's transaction, and a hung migration in
Fly's release_command blocks the whole deploy. So on PROD these two indexes
are pre-created manually via `fly proxy` with
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ...
and this migration's IF NOT EXISTS is then a no-op there. Fresh/dev/CI
databases (small) get them created here normally.
"""
from alembic import op

revision = "0006_axis_score_indexes"
down_revision = "0005_vuln_intel"
branch_labels = None
depends_on = None

# (model_version, axis_name, label, server_id): conditional axis facet counts
# and EXISTS probes become index-only scans on the 458k-row score table.
# (server_id, model_version, axis_name, label): the correlated-EXISTS
# direction (probe by server_id from a registry row).
_INDEXES = (
    ("ix_axis_scores_mv_axis_label_server",
     "mcp_llm_axis_scores (model_version, axis_name, label, server_id)"),
    ("ix_axis_scores_server_mv_axis",
     "mcp_llm_axis_scores (server_id, model_version, axis_name, label)"),
)


def upgrade():
    for name, spec in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")


def downgrade():
    for name, _spec in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
