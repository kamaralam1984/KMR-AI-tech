"""Diagnosis: runs (provider, usage, cost) and the issues they produce.

Issues are keyed by a fingerprint per scan, so diagnosing the same scan again updates the same
rows instead of orphaning anything (repairs and verifications, added later, point at issue ids).

Revision ID: 0004
Revises: 0003
"""

from alembic import context, op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    app = bind.dialect.identifier_preparer.quote(context.config.attributes["app_role"])

    op.execute("""
        CREATE TABLE diagnosis_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            scan_id uuid NOT NULL,
            requested_by uuid NOT NULL REFERENCES users(id),
            provider text NOT NULL,
            model text NOT NULL,
            config_version text NOT NULL,
            status text NOT NULL CHECK (status IN ('completed', 'failed')),
            error_code text,
            input_items int NOT NULL DEFAULT 0,
            input_tokens int,
            output_tokens int,
            cost_micros bigint,
            latency_ms int,
            issues_count int NOT NULL DEFAULT 0,
            dropped_issues int NOT NULL DEFAULT 0,
            flags jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            FOREIGN KEY (scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX ix_diagnosis_runs_org_created ON diagnosis_runs (org_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_diagnosis_runs_scan ON diagnosis_runs (scan_id)")

    op.execute("""
        CREATE TABLE issues (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            project_id uuid NOT NULL,
            scan_id uuid NOT NULL,
            run_id uuid NOT NULL,
            fingerprint text NOT NULL CHECK (length(fingerprint) BETWEEN 8 AND 64),
            title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            category text NOT NULL CHECK (category IN (
                'frontend_error', 'backend_api', 'network', 'accessibility', 'performance',
                'content', 'other')),
            severity text NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
            component text NOT NULL DEFAULT '',
            summary text NOT NULL DEFAULT '',
            state text NOT NULL DEFAULT 'open'
                CHECK (state IN ('open', 'stale', 'fixed', 'not_fixed', 'needs_review')),
            classification text NOT NULL CHECK (classification IN ('diagnosed', 'needs_review')),
            confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
            needs_review_reason text,
            facts jsonb NOT NULL DEFAULT '[]'::jsonb,
            hypotheses jsonb NOT NULL DEFAULT '[]'::jsonb,
            unverified jsonb NOT NULL DEFAULT '[]'::jsonb,
            reproduction_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
            impact text NOT NULL DEFAULT '',
            proposed_fix text NOT NULL DEFAULT '',
            verification_plan text NOT NULL DEFAULT '',
            evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, org_id),
            UNIQUE (scan_id, fingerprint),
            FOREIGN KEY (project_id, org_id) REFERENCES projects (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (scan_id, org_id) REFERENCES scans (id, org_id) ON DELETE CASCADE,
            FOREIGN KEY (run_id, org_id) REFERENCES diagnosis_runs (id, org_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_issues_org_created ON issues (org_id, created_at DESC)")
    op.execute("CREATE INDEX ix_issues_project ON issues (project_id, state)")

    for table in ("diagnosis_runs", "issues"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} FOR ALL "
            "USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org())"
        )
    op.execute(f"GRANT SELECT, INSERT ON diagnosis_runs TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON issues TO {app}")


def downgrade() -> None:
    for table in ("issues", "diagnosis_runs"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
