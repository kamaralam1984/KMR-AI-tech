"""Scanning: site settings on projects, scans, observations, evidence, queue functions.

Tenant tables get the same forced Row-Level Security as in 0001. The scan queue needs two
operations that cannot run inside one tenant's context (claiming a scan by id, and recovering
scans whose worker died), so they are SECURITY DEFINER functions owned by a dedicated role
(`queue_role`, BYPASSRLS, no login). They take no tenant data in and return only ids and
statuses, and nothing else about the schema is opened up.

Revision ID: 0002
Revises: 0001
"""

from alembic import context, op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    quote = bind.dialect.identifier_preparer.quote
    app = quote(context.config.attributes["app_role"])
    queue_name = context.config.attributes["queue_role"]
    queue = quote(queue_name)

    exists = bind.exec_driver_sql(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (queue_name,)
    ).scalar()
    if not exists:
        raise RuntimeError("Queue role does not exist. Run `make db-init` (bootstrap) first.")

    op.execute("ALTER TABLE projects ADD COLUMN site_url text CHECK (length(site_url) <= 2048)")
    op.execute("ALTER TABLE projects ADD COLUMN site_host text")
    op.execute(
        "ALTER TABLE projects ADD COLUMN verification_token text NOT NULL "
        "DEFAULT replace(gen_random_uuid()::text, '-', '')"
    )
    op.execute("ALTER TABLE projects ADD COLUMN verified_at timestamptz")
    op.execute(
        "ALTER TABLE projects ADD COLUMN verified_method text "
        "CHECK (verified_method IN ('dns', 'file'))"
    )
    # The host the ownership proof was made for. If site_host changes, this no longer matches
    # and the project counts as unverified again.
    op.execute("ALTER TABLE projects ADD COLUMN verified_host text")
    op.execute("ALTER TABLE projects ADD COLUMN scan_exclusions text[] NOT NULL DEFAULT '{}'")
    op.execute(
        "ALTER TABLE projects ADD COLUMN scan_max_pages int NOT NULL DEFAULT 10 "
        "CHECK (scan_max_pages BETWEEN 1 AND 50)"
    )
    op.execute("ALTER TABLE projects ADD CONSTRAINT uq_projects_id_org UNIQUE (id, org_id)")

    op.execute("""
        CREATE TABLE scans (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id uuid NOT NULL,
            requested_by uuid NOT NULL REFERENCES users(id),
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            site_url text NOT NULL,
            allowed_host text NOT NULL,
            limits jsonb NOT NULL,
            attempts int NOT NULL DEFAULT 0,
            error_code text,
            stopped_reason text,
            summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key text CHECK (length(idempotency_key) <= 200),
            correlation_id uuid NOT NULL DEFAULT gen_random_uuid(),
            cancel_requested_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            queued_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            heartbeat_at timestamptz,
            finished_at timestamptz,
            UNIQUE (id, org_id),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX ux_scans_idempotency ON scans (project_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    # The database itself guarantees one active scan per project, even under races.
    op.execute(
        "CREATE UNIQUE INDEX ux_scans_one_active ON scans (project_id) "
        "WHERE status IN ('queued', 'running')"
    )
    op.execute("CREATE INDEX ix_scans_org_created ON scans (org_id, created_at DESC)")
    op.execute("CREATE INDEX ix_scans_project_created ON scans (project_id, created_at DESC)")
    op.execute("CREATE INDEX ix_scans_status ON scans (status, heartbeat_at)")

    op.execute("""
        CREATE TABLE evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            scan_id uuid NOT NULL,
            kind text NOT NULL CHECK (kind IN ('screenshot')),
            storage_key text NOT NULL,
            content_type text NOT NULL,
            size_bytes int NOT NULL CHECK (size_bytes >= 0),
            sha256 text NOT NULL,
            page_url text,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_evidence_scan ON evidence (scan_id)")
    op.execute("""
        CREATE TABLE observations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            scan_id uuid NOT NULL,
            kind text NOT NULL CHECK (kind IN (
                'page', 'console_error', 'page_error', 'failed_request', 'http_error',
                'blocked_request', 'a11y_issue', 'limit', 'note')),
            severity text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error')),
            page_url text,
            data jsonb NOT NULL DEFAULT '{}'::jsonb,
            evidence_id uuid REFERENCES evidence (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_observations_scan ON observations (scan_id, created_at)")

    for table in ("scans", "evidence", "observations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON scans TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON observations TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON evidence TO {app}")

    # --- Queue functions, owned by the BYPASSRLS queue role. ---
    op.execute(f"GRANT USAGE ON SCHEMA public TO {queue}")
    op.execute(f"GRANT SELECT, UPDATE ON scans TO {queue}")
    op.execute("""
        CREATE FUNCTION scan_claim(p_scan_id uuid)
        RETURNS TABLE (org_id uuid, project_id uuid, requested_by uuid)
        LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
            UPDATE public.scans
               SET status = 'running',
                   started_at = coalesce(started_at, now()),
                   heartbeat_at = now(),
                   attempts = attempts + 1
             WHERE id = p_scan_id AND status = 'queued' AND cancel_requested_at IS NULL
            RETURNING scans.org_id, scans.project_id, scans.requested_by
        $$
    """)
    op.execute("""
        CREATE FUNCTION scan_recover(
            p_queued_stale interval, p_running_stale interval, p_max_attempts int)
        RETURNS TABLE (scan_id uuid, action text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            -- Running scans whose worker stopped heartbeating: retry once more, or give up.
            RETURN QUERY
            WITH stale AS (
                UPDATE public.scans s
                   SET status = CASE WHEN s.attempts < p_max_attempts
                                     THEN 'queued' ELSE 'failed' END,
                       error_code = CASE WHEN s.attempts < p_max_attempts
                                         THEN s.error_code ELSE 'worker_lost' END,
                       finished_at = CASE WHEN s.attempts < p_max_attempts
                                          THEN NULL ELSE now() END,
                       queued_at = now(),
                       heartbeat_at = NULL
                 WHERE s.status = 'running' AND s.heartbeat_at < now() - p_running_stale
                RETURNING s.id, s.status
            )
            SELECT id, CASE WHEN status = 'queued' THEN 'requeued' ELSE 'failed' END FROM stale;
            -- Queued scans whose queue message never arrived or was lost.
            RETURN QUERY
            SELECT s.id, 'requeue_message'::text FROM public.scans s
             WHERE s.status = 'queued' AND s.cancel_requested_at IS NULL
               AND s.queued_at < now() - p_queued_stale;
        END
        $$
    """)
    for fn in ("scan_claim(uuid)", "scan_recover(interval, interval, int)"):
        op.execute(f"ALTER FUNCTION {fn} OWNER TO {queue}")
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {app}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS scan_recover(interval, interval, int)")
    op.execute("DROP FUNCTION IF EXISTS scan_claim(uuid)")
    for table in ("observations", "evidence", "scans"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS uq_projects_id_org")
    for column in (
        "scan_max_pages",
        "scan_exclusions",
        "verified_host",
        "verified_method",
        "verified_at",
        "verification_token",
        "site_host",
        "site_url",
    ):
        op.execute(f"ALTER TABLE projects DROP COLUMN IF EXISTS {column}")
