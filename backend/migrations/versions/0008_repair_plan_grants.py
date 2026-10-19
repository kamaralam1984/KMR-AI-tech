"""A repair plan's content is immutable for the application role: only `status` may change.

Found in the Phase 10 permission review: 0005 granted UPDATE on every column, but the only update
ever needed is marking an older proposal as superseded.

Revision ID: 0008
Revises: 0007
"""

from alembic import context, op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    op.execute(f"REVOKE UPDATE ON repair_plans FROM {app}")
    op.execute(f"GRANT UPDATE (status) ON repair_plans TO {app}")


def downgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    op.execute(f"REVOKE UPDATE (status) ON repair_plans FROM {app}")
    op.execute(f"GRANT UPDATE ON repair_plans TO {app}")
