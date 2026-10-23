"""Give users a Clerk identity, so a signup can reach the database at all.

THE GAP THIS CLOSES
-------------------
Clerk has been the front door since the June cutover, but only in the browser:
`app/main.py` injects `CLERK_PUBLISHABLE_KEY` into the static pages and the CSP
allows `*.clerk.com`. Nothing on the server side ever hears about a signup. A
person who completes the Clerk flow exists in Clerk and NOWHERE in our Postgres,
so `users` has never held a single real customer -- only rows made by
`/auth/register` and the deterministic `/auth/oauth/{provider}/callback` stub.

Three columns, all NULLABLE, so the table keeps working unchanged for the
password and OAuth-stub paths that have no Clerk identity:

  clerk_id          the `user_xxx` Clerk primary key. UNIQUE -- it is the
                    idempotency key for the webhook. Clerk retries on any
                    non-2xx and Svix redelivers for days, so "the same signup
                    arriving five times" is the NORMAL case here, not the edge
                    case, and it has to be a no-op at the database level rather
                    than at the application's discretion.

  clerk_synced_via  'webhook' | 'reconcile'. This exists to be a NEGATIVE
                    CONTROL, not a curiosity. A webhook that silently stops
                    firing looks exactly like a product with no signups, and
                    across a 23-day unattended window those two readings are
                    worth very different things. The nightly reconcile backfills
                    from the Clerk API and stamps 'reconcile'; any row it has to
                    create for a signup older than the alert threshold is proof
                    the webhook did not deliver. Silence stops being ambiguous.

  clerk_created_at  the signup time CLERK reports, which is not the time our row
                    was written. Without it a backfilled user is
                    indistinguishable from a live one and the control above
                    cannot compute the delivery gap it depends on.

Deliberately NOT done here: no backfill of existing rows, no NOT NULL, no
default-org rewrite. This migration is additive and reversible -- `downgrade`
drops exactly what `upgrade` added -- which is what keeps the prod-drift lane's
hazard classification at Class A rather than a migration-bearing Class B that
stays attended-only for the whole window.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_clerk_identity"
down_revision = "0010_canonical_family"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("clerk_id", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("clerk_synced_via", sa.String(16), nullable=True))
    op.add_column("users", sa.Column("clerk_created_at", sa.DateTime(), nullable=True))
    # UNIQUE rather than a plain index: the constraint IS the idempotency
    # guarantee. An index would make a duplicate fast to find and still let two
    # concurrent redeliveries both insert.
    op.create_index("uq_users_clerk_id", "users", ["clerk_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_clerk_id", table_name="users")
    op.drop_column("users", "clerk_created_at")
    op.drop_column("users", "clerk_synced_via")
    op.drop_column("users", "clerk_id")
