"""Post-fix verification runs: a before scan, an after scan, and an honest outcome.

Revision ID: 0007
Revises: 0006
"""

from alembic import context, op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    op.execute("""
        CREATE TABLE verification_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            project_id uuid NOT NULL,
            issue_id uuid NOT NULL,
            repair_id uuid,
            application_id uuid,
            before_scan_id uuid NOT NULL,
            after_scan_id uuid NOT NULL,
            requested_by uuid NOT NULL REFERENCES users(id),
            outcome text NOT NULL CHECK (outcome IN ('fixed', 'not_fixed', 'needs_review')),
            reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            verified_diff_sha256 text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (issue_id, org_id) REFERENCES issues (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (before_scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (after_scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX ix_verification_issue ON verification_runs (issue_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_verification_org_created ON verification_runs (org_id, created_at DESC)"
    )
    op.execute("ALTER TABLE verification_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE verification_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY verification_runs_tenant ON verification_runs FOR ALL "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )
    # Append-only: a verification result is a record, not something to edit.
    op.execute(f"GRANT SELECT, INSERT ON verification_runs TO {app}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification_runs CASCADE")
