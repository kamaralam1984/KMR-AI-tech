"""Customer-supplied traffic for impact ranking (12A) and records of tickets created outside (12B).

traffic_imports holds page-view counts per path that the owner exported from their own analytics
and uploaded. KMR AI does not measure or verify them; every use of them is labeled
"customer-supplied". There is no revenue or conversion column: nothing here can produce such a
number.

issue_exports remembers that a finding was turned into an external ticket (a GitHub issue), so the
same finding cannot be sent twice and the audit trail shows exactly which text was sent.

Revision ID: 0012
Revises: 0011
"""

from alembic import context, op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])

    op.execute("""
        CREATE TABLE traffic_imports (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id uuid NOT NULL,
            path text NOT NULL CHECK (left(path, 1) = '/' AND length(path) <= 300),
            views bigint NOT NULL CHECK (views >= 0),
            period_start date NOT NULL,
            period_end date NOT NULL CHECK (period_end >= period_start),
            source_label text NOT NULL CHECK (length(btrim(source_label)) BETWEEN 1 AND 100),
            imported_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_traffic_imports_project ON traffic_imports (project_id)")

    op.execute("""
        CREATE TABLE issue_exports (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            issue_id uuid NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('github_issue')),
            external_url text NOT NULL CHECK (length(external_url) <= 500),
            external_number int,
            content_sha256 text NOT NULL CHECK (length(content_sha256) = 64),
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (issue_id, kind)
        )
    """)

    for table in ("traffic_imports", "issue_exports"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON traffic_imports TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON issue_exports TO {app}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS issue_exports CASCADE")
    op.execute("DROP TABLE IF EXISTS traffic_imports CASCADE")
