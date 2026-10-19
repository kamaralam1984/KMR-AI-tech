"""Initial schema: users, organizations, memberships, projects, sessions, audit events.

Tenant isolation is enforced in two layers: the API checks membership and role, and PostgreSQL
Row-Level Security (FORCEd, so it applies to table owners too) restricts every tenant table to
the organization set in the transaction-local `app.current_org_id`. The application connects as
a non-superuser role with no BYPASSRLS; this migration only grants it what it needs.

Revision ID: 0001
Revises:
"""

from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])

    exists = bind.exec_driver_sql(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (context.config.attributes["app_role"],)
    ).scalar()
    if not exists:
        raise RuntimeError(
            "App role does not exist. Run `python -m app.db.bootstrap` first (make db-init)."
        )

    op.execute("""
        CREATE FUNCTION app_current_user() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT nullif(current_setting('app.current_user_id', true), '')::uuid $$
    """)
    op.execute("""
        CREATE FUNCTION app_current_org() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT nullif(current_setting('app.current_org_id', true), '')::uuid $$
    """)

    op.execute("""
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL UNIQUE CHECK (email = lower(email) AND length(email) <= 320),
            display_name text NOT NULL DEFAULT '' CHECK (length(display_name) <= 100),
            password_hash text NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE organizations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE memberships (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role varchar(16) NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, user_id)
        )
    """)
    op.execute("CREATE INDEX ix_memberships_user_id ON memberships (user_id)")
    op.execute("""
        CREATE TABLE projects (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_projects_org_created ON projects (org_id, created_at)")
    op.execute("""
        CREATE TABLE auth_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash bytea NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            idle_expires_at timestamptz NOT NULL,
            absolute_expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            user_agent text
        )
    """)
    op.execute("CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("""
        CREATE TABLE audit_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid REFERENCES organizations(id),
            actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            action text NOT NULL,
            target_type text,
            target_id text,
            outcome varchar(16) NOT NULL CHECK (outcome IN ('success','denied','failure')),
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_audit_events_org_created ON audit_events (org_id, created_at)")

    # --- Row-Level Security on every tenant table, enforced even for the table owner. ---
    for table in ("organizations", "memberships", "projects", "audit_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # A user sees an organization if it is the active one or they belong to it.
    op.execute("""
        CREATE POLICY org_select ON organizations FOR SELECT USING (
            id = app_current_org()
            OR EXISTS (SELECT 1 FROM memberships m
                       WHERE m.org_id = organizations.id AND m.user_id = app_current_user())
        )
    """)
    # Creating an organization requires first setting it as the active one (see tenant.py).
    op.execute(
        "CREATE POLICY org_insert ON organizations FOR INSERT WITH CHECK (id = app_current_org())"
    )
    op.execute(
        "CREATE POLICY org_update ON organizations FOR UPDATE "
        "USING (id = app_current_org()) WITH CHECK (id = app_current_org())"
    )

    # A user can always see their own memberships (to list their organizations);
    # everything else only within the active organization.
    op.execute(
        "CREATE POLICY m_select ON memberships FOR SELECT "
        "USING (org_id = app_current_org() OR user_id = app_current_user())"
    )
    op.execute(
        "CREATE POLICY m_insert ON memberships FOR INSERT WITH CHECK (org_id = app_current_org())"
    )
    op.execute(
        "CREATE POLICY m_update ON memberships FOR UPDATE "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )
    op.execute(
        "CREATE POLICY m_delete ON memberships FOR DELETE USING (org_id = app_current_org())"
    )

    op.execute(
        "CREATE POLICY p_all ON projects FOR ALL "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )

    # Audit rows with no organization (for example failed logins) are not readable by the app.
    op.execute(
        "CREATE POLICY a_select ON audit_events FOR SELECT USING (org_id = app_current_org())"
    )
    op.execute(
        "CREATE POLICY a_insert ON audit_events FOR INSERT "
        "WITH CHECK (org_id IS NULL OR org_id = app_current_org())"
    )

    # --- Least-privilege grants. audit_events is append-only for the app role. ---
    op.execute(f"GRANT USAGE ON SCHEMA public TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON organizations TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON projects TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON auth_sessions TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {app}")


def downgrade() -> None:
    for table in (
        "audit_events",
        "auth_sessions",
        "projects",
        "memberships",
        "organizations",
        "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS app_current_org()")
    op.execute("DROP FUNCTION IF EXISTS app_current_user()")
