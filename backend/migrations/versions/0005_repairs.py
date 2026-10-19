"""Repository connections and repair plans.

A repair plan stores the exact proposed file contents and the unified diff with its SHA-256. The
plan is bound to the base commit it was made against. Nothing here changes a repository: applying
a plan needs an approval bound to that diff hash (added in 0006).

Revision ID: 0005
Revises: 0004
"""

from alembic import context, op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])

    op.execute("""
        CREATE TABLE repository_connections (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            project_id uuid NOT NULL,
            provider text NOT NULL CHECK (provider IN ('local_git', 'github')),
            location text NOT NULL CHECK (length(location) BETWEEN 1 AND 500),
            default_branch text NOT NULL CHECK (length(default_branch) BETWEEN 1 AND 200),
            token_ct bytea,
            check_command text CHECK (check_command IS NULL OR length(check_command) <= 500),
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            UNIQUE (project_id),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE
        )
    """)

    op.execute("""
        CREATE TABLE repair_plans (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            project_id uuid NOT NULL,
            issue_id uuid NOT NULL,
            connection_id uuid NOT NULL,
            requested_by uuid NOT NULL REFERENCES users(id),
            status text NOT NULL CHECK (status IN ('proposed', 'no_fix', 'rejected', 'superseded')),
            source text NOT NULL CHECK (source IN ('rules', 'ai', 'manual')),
            provider text NOT NULL,
            model text NOT NULL,
            base_branch text NOT NULL,
            base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
            branch_name text NOT NULL,
            explanation text NOT NULL DEFAULT '',
            changes jsonb NOT NULL DEFAULT '[]'::jsonb,
            diff text NOT NULL DEFAULT '',
            diff_sha256 text NOT NULL DEFAULT '' CHECK (diff_sha256 ~ '^([0-9a-f]{64})?$'),
            findings jsonb NOT NULL DEFAULT '[]'::jsonb,
            warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (issue_id, org_id) REFERENCES issues (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (connection_id, org_id)
                REFERENCES repository_connections (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_repair_plans_issue ON repair_plans (issue_id, created_at DESC)")
    op.execute("CREATE INDEX ix_repair_plans_org_created ON repair_plans (org_id, created_at DESC)")

    for table in ("repository_connections", "repair_plans"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON repository_connections TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON repair_plans TO {app}")


def downgrade() -> None:
    for table in ("repair_plans", "repository_connections"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
