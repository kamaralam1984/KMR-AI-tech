"""Live browser agent (Phase 13): extension tokens.

An extension token lets the Chrome extension send redacted page evidence for one organization. It
is a bearer secret that acts as the user who created it, so:
- only a SHA-256 of it is stored; the plaintext is shown once;
- it expires (at most 90 days) and can be revoked at any time;
- on every use the server re-checks that the user still belongs to the organization.
Requests carrying a token are tied to their tenant through `extension_resolve`, a narrow SECURITY
DEFINER lookup by hash; after that every query runs under forced Row-Level Security.

Revision ID: 0010
Revises: 0009
"""

from alembic import context, op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    queue = bind.dialect.identifier_preparer.quote(context.config.attributes["queue_role"])

    op.execute("""
        CREATE TABLE extension_tokens (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
            token_hash bytea NOT NULL UNIQUE,
            token_prefix text NOT NULL,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            last_used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '91 days')
        )
    """)
    op.execute("CREATE INDEX ix_extension_tokens_org ON extension_tokens (org_id, created_at DESC)")
    op.execute("ALTER TABLE extension_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE extension_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY extension_tokens_tenant ON extension_tokens FOR ALL "
        "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
    )
    op.execute(f"GRANT SELECT, INSERT ON extension_tokens TO {app}")
    op.execute(f"GRANT UPDATE (revoked_at, last_used_at) ON extension_tokens TO {app}")

    op.execute(f"GRANT SELECT ON extension_tokens TO {queue}")
    op.execute("""
        CREATE FUNCTION extension_resolve(p_hash bytea)
        RETURNS TABLE (token_id uuid, org_id uuid, user_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT t.id, t.org_id, t.user_id
              FROM public.extension_tokens t
             WHERE t.token_hash = p_hash AND t.revoked_at IS NULL AND t.expires_at > now()
        $$
    """)
    op.execute(f"ALTER FUNCTION extension_resolve(bytea) OWNER TO {queue}")
    op.execute("REVOKE ALL ON FUNCTION extension_resolve(bytea) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION extension_resolve(bytea) TO {app}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS extension_resolve(bytea)")
    op.execute("DROP TABLE IF EXISTS extension_tokens CASCADE")
