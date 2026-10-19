"""Check runs, approvals bound to an exact change, applications, and a tamper-evident audit chain.

An approval stores the hash of everything it authorizes (repository, branch, base commit, issues,
diff hash, action, approver). The app role may only update the revoke/consume columns of an
approval, never the bound values. Audit rows of an organization form a hash chain: each row's hash
covers the previous row's hash, so editing, deleting or reordering a row breaks verification.

Revision ID: 0006
Revises: 0005
"""

from alembic import context, op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])

    op.execute(
        "ALTER TABLE audit_events ADD COLUMN chain_seq bigint, "
        "ADD COLUMN prev_hash text, ADD COLUMN entry_hash text"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_audit_chain ON audit_events (org_id, chain_seq) "
        "WHERE chain_seq IS NOT NULL"
    )

    op.execute("""
        CREATE TABLE check_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            repair_id uuid NOT NULL,
            requested_by uuid NOT NULL REFERENCES users(id),
            diff_sha256 text NOT NULL CHECK (diff_sha256 ~ '^[0-9a-f]{64}$'),
            base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
            overall text NOT NULL CHECK (overall IN ('passed', 'failed', 'unavailable', 'not_run')),
            results jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (repair_id, org_id) REFERENCES repair_plans (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_check_runs_repair ON check_runs (repair_id, created_at DESC)")

    op.execute("""
        CREATE TABLE approvals (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            repair_id uuid NOT NULL,
            project_id uuid NOT NULL,
            approver_id uuid NOT NULL REFERENCES users(id),
            decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
            action text NOT NULL CHECK (action IN ('push_branch')),
            issue_ids jsonb NOT NULL,
            repo_provider text NOT NULL,
            repo_location text NOT NULL,
            base_branch text NOT NULL,
            branch_name text NOT NULL,
            base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
            diff_sha256 text NOT NULL CHECK (diff_sha256 ~ '^[0-9a-f]{64}$'),
            bound_hash text NOT NULL CHECK (bound_hash ~ '^[0-9a-f]{64}$'),
            check_run_id uuid,
            acknowledged_incomplete_checks boolean NOT NULL DEFAULT false,
            note text NOT NULL DEFAULT '',
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            revoked_by uuid REFERENCES users(id),
            revoked_reason text,
            consumed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (repair_id, org_id) REFERENCES repair_plans (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (check_run_id, org_id) REFERENCES check_runs (id, org_id)
        )
    """)
    op.execute("CREATE INDEX ix_approvals_repair ON approvals (repair_id, created_at DESC)")

    op.execute("""
        CREATE TABLE repair_applications (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            repair_id uuid NOT NULL,
            approval_id uuid NOT NULL,
            applied_by uuid NOT NULL REFERENCES users(id),
            status text NOT NULL CHECK (status IN ('applied', 'failed')),
            branch_name text NOT NULL,
            commit_sha text,
            base_sha text NOT NULL,
            diff_sha256 text NOT NULL,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (repair_id, org_id) REFERENCES repair_plans (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (approval_id, org_id) REFERENCES approvals (id, org_id)
        )
    """)
    # An approval can produce at most one SUCCESSFUL application.
    op.execute(
        "CREATE UNIQUE INDEX ux_application_once ON repair_applications (approval_id) "
        "WHERE status = 'applied'"
    )

    for table in ("check_runs", "approvals", "repair_applications"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )
    op.execute(f"GRANT SELECT, INSERT ON check_runs TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON repair_applications TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON approvals TO {app}")
    # Only these columns can ever change after an approval is created.
    op.execute(
        f"GRANT UPDATE (revoked_at, revoked_by, revoked_reason, consumed_at) ON approvals TO {app}"
    )


def downgrade() -> None:
    for table in ("repair_applications", "approvals", "check_runs"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP INDEX IF EXISTS ux_audit_chain")
    op.execute(
        "ALTER TABLE audit_events DROP COLUMN IF EXISTS chain_seq, "
        "DROP COLUMN IF EXISTS prev_hash, DROP COLUMN IF EXISTS entry_hash"
    )
