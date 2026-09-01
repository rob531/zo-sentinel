"""Declare two tables the app has always used and never owned.

WHAT WAS ACTUALLY WRONG
-----------------------
`api_usage` and `app_stats` were on the phantom-table list in #4080 as two of
"18 phantom table names" needing a real fix. They are not phantoms and there is
no better name to give them. They are REAL tables that the app reads and writes
on every request, and nothing in the repository declared them.

  api_usage   verdict_breakdown_api.py:charge_lookup() and /me. This is the
              DAILY LOOKUP CAP -- the row that decides whether a public user is
              allowed another verdict. It is written with ON CONFLICT (user_id,
              day) DO UPDATE, so the composite primary key is not decoration:
              without it every lookup would INSERT a new row and the cap would
              never bind.

  app_stats   verdict_breakdown_api.py:/dashboard/summary. The precomputed
              dashboard aggregate; the live computation takes ~40s on the Fly
              PG tier, so the endpoint serves this row and warms it in a
              background thread on a cold cache.

WHY NOBODY NOTICED
------------------
tests/ci/smoke_seed.py issues `CREATE TABLE IF NOT EXISTS` for both before the
smoke ladder runs, so CI has always had them. Production has them because
something created them there once. Neither fact is in version control as a
schema, so `tools/referent_verify.py` -- which resolves against app/models.py
and this directory -- correctly reported that they exist on no declared plane.

That is not a false positive. A table whose existence depends on a test fixture
having run is a table that a fresh deployment does not have, and the failure
mode is not a crash: `charge_lookup` would raise inside a request path and the
daily cap would be the thing that broke.

WHY A MIGRATION AND NOT A MODEL
-------------------------------
Neither table has an ORM class and neither wants one -- both are reached with
raw `text()` for the ON CONFLICT upsert and the ~40s aggregate cache. A
migration declares them on a plane the checker reads without inventing mappers
that nothing would use.

`IF NOT EXISTS` semantics via checkfirst: production and CI already have these,
so this must be a no-op there and a create on a fresh database.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_declare_api_usage_app_stats"
down_revision = "0011_clerk_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "api_usage" not in existing:
        op.create_table(
            "api_usage",
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("lookups", sa.Integer(), nullable=False, server_default="0"),
            # Composite PK, not a surrogate id: it IS the conflict target of the
            # ON CONFLICT (user_id, day) DO UPDATE in charge_lookup(). A
            # surrogate key would make that upsert a silent insert and the daily
            # cap would stop binding.
            sa.PrimaryKeyConstraint("user_id", "day", name="pk_api_usage"),
        )

    if "app_stats" not in existing:
        op.create_table(
            "app_stats",
            sa.Column("key", sa.Text(), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    # Additive and reversible, matching 0011. Dropping api_usage discards the
    # day's lookup counters, which is the correct behaviour for a downgrade:
    # the cap resets rather than the schema lying about what it counts.
    op.drop_table("app_stats")
    op.drop_table("api_usage")
