"""Visitor assistant (Phase 11): widget installs, knowledge base, form definitions, handoffs.

A widget install is the per-tenant credential a company puts on its own site. Only a SHA-256 of
its key is stored. Visitors have no account, so a public request is tied to its tenant through
`assistant_resolve`, a narrow SECURITY DEFINER lookup by key hash; after that every query runs
under the same forced Row-Level Security as any other tenant data.

Visitor text is never stored, except an explicitly consented hand-off summary, which carries an
expiry date and is deleted by `assistant_purge_expired` (and by the tenant at any time).

Revision ID: 0009
Revises: 0008
"""

from alembic import context, op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])
    queue = bind.dialect.identifier_preparer.quote(context.config.attributes["queue_role"])

    op.execute("""
        CREATE TABLE widget_installs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id uuid,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
            key_hash bytea NOT NULL UNIQUE,
            key_prefix text NOT NULL,
            allowed_origins text[] NOT NULL DEFAULT '{}',
            modules text[] NOT NULL DEFAULT '{guide}'
                CHECK (modules <@ ARRAY['guide', 'forms', 'learning']::text[]),
            enabled boolean NOT NULL DEFAULT true,
            consent_text text NOT NULL DEFAULT '' CHECK (length(consent_text) <= 1000),
            handoff_retention_days int NOT NULL DEFAULT 30
                CHECK (handoff_retention_days BETWEEN 1 AND 365),
            learning_ack_at timestamptz,
            learning_ack_by uuid REFERENCES users(id),
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            revoked_at timestamptz,
            UNIQUE (id, org_id),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE SET NULL
                (project_id),
            CHECK (NOT ('learning' = ANY (modules)) OR learning_ack_at IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX ix_widget_installs_org ON widget_installs (org_id, created_at DESC)")

    op.execute("""
        CREATE TABLE kb_pages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            install_id uuid NOT NULL,
            kind text NOT NULL CHECK (kind IN ('site', 'learning')),
            url text CHECK (url IS NULL OR length(url) <= 500),
            title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
            content text NOT NULL CHECK (length(content) <= 200000),
            content_sha256 text NOT NULL,
            source text NOT NULL CHECK (source IN ('manual', 'indexed')),
            chunk_count int NOT NULL DEFAULT 0,
            blocked_chunks int NOT NULL DEFAULT 0,
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (install_id, org_id) REFERENCES widget_installs (id, org_id)
                ON DELETE CASCADE
        )
    """)
    op.execute("CREATE UNIQUE INDEX ux_kb_pages_url ON kb_pages (install_id, kind, url)")
    op.execute("CREATE INDEX ix_kb_pages_install ON kb_pages (install_id, kind)")

    op.execute("""
        CREATE TABLE kb_chunks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            install_id uuid NOT NULL,
            page_id uuid NOT NULL,
            kind text NOT NULL,
            idx int NOT NULL,
            text text NOT NULL,
            tokens text[] NOT NULL,
            blocked boolean NOT NULL DEFAULT false,
            FOREIGN KEY (page_id, org_id) REFERENCES kb_pages (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_kb_chunks_tokens ON kb_chunks USING gin (tokens)")
    op.execute("CREATE INDEX ix_kb_chunks_install ON kb_chunks (install_id, kind)")

    op.execute("""
        CREATE TABLE form_definitions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            install_id uuid NOT NULL,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
            category text NOT NULL,
            definition jsonb NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (install_id, org_id) REFERENCES widget_installs (id, org_id)
                ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_form_definitions_install ON form_definitions (install_id)")

    op.execute("""
        CREATE TABLE handoffs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            install_id uuid NOT NULL,
            summary text NOT NULL CHECK (length(summary) BETWEEN 1 AND 2000),
            contact text CHECK (contact IS NULL OR length(contact) <= 200),
            consent_at timestamptz NOT NULL,
            status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'resolved')),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (install_id, org_id) REFERENCES widget_installs (id, org_id)
                ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_handoffs_install ON handoffs (install_id, created_at DESC)")
    op.execute("CREATE INDEX ix_handoffs_expiry ON handoffs (expires_at)")

    for table in ("widget_installs", "kb_pages", "kb_chunks", "form_definitions", "handoffs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON widget_installs TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON kb_pages TO {app}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON kb_chunks TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON form_definitions TO {app}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON handoffs TO {app}")
    op.execute(f"GRANT UPDATE (status) ON handoffs TO {app}")

    # --- Narrow cross-tenant functions, owned by the BYPASSRLS queue role. ---
    op.execute(f"GRANT SELECT ON widget_installs TO {queue}")
    op.execute(f"GRANT SELECT, DELETE ON handoffs TO {queue}")
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
    op.execute("""
        CREATE FUNCTION assistant_purge_expired() RETURNS int
        LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
            WITH gone AS (DELETE FROM public.handoffs WHERE expires_at < now() RETURNING 1)
            SELECT count(*)::int FROM gone
        $$
    """)
    for fn in ("assistant_resolve(bytea)", "assistant_purge_expired()"):
        op.execute(f"ALTER FUNCTION {fn} OWNER TO {queue}")
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {app}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS assistant_purge_expired()")
    op.execute("DROP FUNCTION IF EXISTS assistant_resolve(bytea)")
    for table in ("handoffs", "form_definitions", "kb_chunks", "kb_pages", "widget_installs"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
