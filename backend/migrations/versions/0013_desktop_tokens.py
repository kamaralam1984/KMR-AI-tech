"""Desktop assistant tokens (Phase 14): a token now has a kind.

The desktop assistant authenticates like the browser extension, with a bearer token that acts as its
user, is stored only as a hash, expires and can be revoked. A token is valid for ONE kind of client:
an extension token cannot call the desktop API and a desktop token cannot upload page evidence.

Revision ID: 0013
Revises: 0012
"""

from alembic import context, op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    queue = bind.dialect.identifier_preparer.quote(context.config.attributes["queue_role"])

    op.execute(
        "ALTER TABLE extension_tokens ADD COLUMN kind text NOT NULL DEFAULT 'extension' "
        "CHECK (kind IN ('extension', 'desktop'))"
    )
    op.execute("DROP FUNCTION extension_resolve(bytea)")
    op.execute("""
        CREATE FUNCTION extension_resolve(p_hash bytea)
        RETURNS TABLE (token_id uuid, org_id uuid, user_id uuid, kind text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT t.id, t.org_id, t.user_id, t.kind
              FROM public.extension_tokens t
             WHERE t.token_hash = p_hash AND t.revoked_at IS NULL AND t.expires_at > now()
        $$
    """)
    op.execute(f"ALTER FUNCTION extension_resolve(bytea) OWNER TO {queue}")
    op.execute("REVOKE ALL ON FUNCTION extension_resolve(bytea) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION extension_resolve(bytea) TO {app}")


def downgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    queue = bind.dialect.identifier_preparer.quote(context.config.attributes["queue_role"])
    op.execute("DROP FUNCTION IF EXISTS extension_resolve(bytea)")
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
    op.execute("ALTER TABLE extension_tokens DROP COLUMN IF EXISTS kind")
