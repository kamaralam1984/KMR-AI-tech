"""Desktop assistant steps (Phase 14): a record that a step was offered, and how it ended.

Only metadata is kept: which kind of step, when, and the outcome the person's own app reported.
Never the window, the element, the text or what the person typed. The record lets the audit trail
show "a press step was offered, then refused" without storing anything from the screen, and lets
the server accept an outcome only for a step it really issued, once.

Revision ID: 0014
Revises: 0013
"""

from alembic import context, op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    op.execute("""
        CREATE TABLE desk_steps (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_id uuid NOT NULL REFERENCES extension_tokens(id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('highlight', 'press', 'type')),
            outcome text CHECK (outcome IN ('executed', 'refused', 'rejected', 'expired', 'failed')),
            created_at timestamptz NOT NULL DEFAULT now(),
            reported_at timestamptz
        )
    """)
    op.execute("CREATE INDEX ix_desk_steps_org ON desk_steps (org_id, created_at DESC)")
    op.execute("ALTER TABLE desk_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE desk_steps FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY desk_steps_tenant ON desk_steps FOR ALL "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )
    op.execute(f"GRANT SELECT, INSERT ON desk_steps TO {app}")
    op.execute(f"GRANT UPDATE (outcome, reported_at) ON desk_steps TO {app}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS desk_steps CASCADE")
