"""Backend evidence: API checks, their runs, customer-supplied backend logs.

Everything here is tenant data under the same forced Row-Level Security as before. API-check
credentials are stored only as ciphertext (see app/security/secrets_box.py).

Revision ID: 0003
Revises: 0002
"""

from alembic import context, op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])

    op.execute("ALTER TABLE projects ADD COLUMN inject_request_id boolean NOT NULL DEFAULT true")

    op.execute("ALTER TABLE observations DROP CONSTRAINT observations_kind_check")
    op.execute("""
        ALTER TABLE observations ADD CONSTRAINT observations_kind_check CHECK (kind IN (
            'page', 'console_error', 'page_error', 'failed_request', 'http_error',
            'blocked_request', 'a11y_issue', 'limit', 'note', 'api_check'))
    """)

    op.execute("""
        CREATE TABLE api_checks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            project_id uuid NOT NULL,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
            path text NOT NULL CHECK (left(path, 1) = '/' AND length(path) <= 1024),
            method text NOT NULL DEFAULT 'GET' CHECK (method IN ('GET', 'HEAD')),
            expected_status int NOT NULL DEFAULT 200 CHECK (expected_status BETWEEN 100 AND 599),
            max_latency_ms int NOT NULL DEFAULT 3000 CHECK (max_latency_ms BETWEEN 50 AND 30000),
            response_schema jsonb,
            auth_header_name text CHECK (auth_header_name ~ '^[A-Za-z][A-Za-z0-9-]{0,63}$'),
            auth_secret_ct bytea,
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            UNIQUE (project_id, name),
            CHECK ((auth_header_name IS NULL) = (auth_secret_ct IS NULL)),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_api_checks_project ON api_checks (project_id)")

    op.execute("""
        CREATE TABLE api_check_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            check_id uuid NOT NULL,
            scan_id uuid,
            triggered_by text NOT NULL CHECK (triggered_by IN ('manual', 'scan')),
            status text NOT NULL CHECK (status IN ('passed', 'failed', 'error')),
            http_status int,
            latency_ms int,
            response_bytes int,
            reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
            request_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (check_id, org_id) REFERENCES api_checks (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_api_check_runs_check ON api_check_runs (check_id, created_at DESC)")
    op.execute("CREATE INDEX ix_api_check_runs_scan ON api_check_runs (scan_id)")

    op.execute("""
        CREATE TABLE backend_log_entries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            scan_id uuid NOT NULL,
            ts timestamptz,
            level text,
            request_id text,
            http_status int,
            path text,
            message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX ix_backend_logs_scan_request ON backend_log_entries (scan_id, request_id)"
    )

    for table in ("api_checks", "api_check_runs", "backend_log_entries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_checks TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON api_check_runs TO {app}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON backend_log_entries TO {app}")


def downgrade() -> None:
    for table in ("backend_log_entries", "api_check_runs", "api_checks"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DELETE FROM observations WHERE kind = 'api_check'")
    op.execute("ALTER TABLE observations DROP CONSTRAINT observations_kind_check")
    op.execute("""
        ALTER TABLE observations ADD CONSTRAINT observations_kind_check CHECK (kind IN (
            'page', 'console_error', 'page_error', 'failed_request', 'http_error',
            'blocked_request', 'a11y_issue', 'limit', 'note'))
    """)
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS inject_request_id")
