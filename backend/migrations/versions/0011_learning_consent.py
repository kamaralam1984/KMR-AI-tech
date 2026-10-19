"""Verifiable guardian consent for the learning module (Phase 11C).

A browser checkbox is only a statement. Here the organization that installs the widget records, per
learner reference, that it collected consent in its own process (`consent_basis`), and receives a
signed token for it. Learners under 18 can only use the module with a valid token: not expired, not
revoked, issued for this install, and covering the learner's age band. No learner name, birth date
or contact detail is stored: `label` is the installer's own opaque reference.

Revision ID: 0011
Revises: 0010
"""

from alembic import context, op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    queue = bind.dialect.identifier_preparer.quote(context.config.attributes["queue_role"])

    op.execute(
        "ALTER TABLE widget_installs ADD COLUMN learning_consent_required boolean "
        "NOT NULL DEFAULT true"
    )
    op.execute("""
        CREATE TABLE learning_consents (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            install_id uuid NOT NULL,
            label text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 60),
            age_band text NOT NULL CHECK (age_band IN ('6-8', '9-11', '12-14', '15-17')),
            consent_basis text NOT NULL CHECK (length(btrim(consent_basis)) BETWEEN 5 AND 300),
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '367 days'),
            UNIQUE (id, org_id),
            FOREIGN KEY (install_id, org_id) REFERENCES widget_installs (id, org_id)
                ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_learning_consents_install ON learning_consents (install_id)")
    op.execute("ALTER TABLE learning_consents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE learning_consents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY learning_consents_tenant ON learning_consents FOR ALL "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )
    op.execute(f"GRANT SELECT, INSERT ON learning_consents TO {app}")
    op.execute(f"GRANT UPDATE (revoked_at) ON learning_consents TO {app}")
    op.execute(f"GRANT UPDATE (learning_consent_required) ON widget_installs TO {app}")

    # The lookup function gains one column, which changes its return type.
    op.execute("DROP FUNCTION assistant_resolve(bytea)")
    op.execute("""
        CREATE FUNCTION assistant_resolve(p_key_hash bytea)
        RETURNS TABLE (
            install_id uuid, org_id uuid, project_id uuid, name text, allowed_origins text[],
            modules text[], enabled boolean, consent_text text, retention_days int,
            consent_required boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT w.id, w.org_id, w.project_id, w.name, w.allowed_origins, w.modules,
                   w.enabled, w.consent_text, w.handoff_retention_days,
                   w.learning_consent_required
              FROM public.widget_installs w
             WHERE w.key_hash = p_key_hash AND w.revoked_at IS NULL
        $$
    """)
    op.execute(f"ALTER FUNCTION assistant_resolve(bytea) OWNER TO {queue}")
    op.execute("REVOKE ALL ON FUNCTION assistant_resolve(bytea) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION assistant_resolve(bytea) TO {app}")


def downgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    queue = bind.dialect.identifier_preparer.quote(context.config.attributes["queue_role"])
    op.execute("DROP FUNCTION IF EXISTS assistant_resolve(bytea)")
    op.execute("""
        CREATE FUNCTION assistant_resolve(p_key_hash bytea)
        RETURNS TABLE (
            install_id uuid, org_id uuid, project_id uuid, name text, allowed_origins text[],
            modules text[], enabled boolean, consent_text text, retention_days int)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT w.id, w.org_id, w.project_id, w.name, w.allowed_origins, w.modules,
                   w.enabled, w.consent_text, w.handoff_retention_days
              FROM public.widget_installs w
             WHERE w.key_hash = p_key_hash AND w.revoked_at IS NULL
        $$
    """)
    op.execute(f"ALTER FUNCTION assistant_resolve(bytea) OWNER TO {queue}")
    op.execute("REVOKE ALL ON FUNCTION assistant_resolve(bytea) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION assistant_resolve(bytea) TO {app}")
    op.execute("DROP TABLE IF EXISTS learning_consents CASCADE")
    op.execute("ALTER TABLE widget_installs DROP COLUMN IF EXISTS learning_consent_required")
