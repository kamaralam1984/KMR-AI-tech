"""Local development helpers. Refuses to run unless KMR_APP_ENV is "local".

`mark-verified <project_id>` records an ownership proof WITHOUT checking DNS or a file. It exists so
the browser end-to-end test can scan a public demo site that nobody here owns. Production has no
such path: the API only verifies through DNS or a file, and this tool will not run there.
"""

import sys
import uuid

import psycopg

from app.config import get_settings


def mark_verified(project_id: uuid.UUID) -> str:
    settings = get_settings()
    if settings.app_env != "local":
        raise SystemExit("Refusing to run: KMR_APP_ENV is not 'local'.")
    if settings.migration_database_url is None:
        raise SystemExit("Set KMR_MIGRATION_DATABASE_URL in .env")
    url = settings.migration_database_url.get_secret_value().replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "UPDATE projects SET verified_at = now(), verified_method = 'dns', "
            "verified_host = site_host WHERE id = %s AND site_host IS NOT NULL "
            "RETURNING org_id, site_host",
            (project_id,),
        ).fetchone()
        if row is None:
            raise SystemExit("No such project, or it has no site address yet.")
        conn.execute(
            "INSERT INTO audit_events "
            "(id, org_id, action, outcome, target_type, target_id, details) "
            "VALUES (%s, %s, 'domain.verify', 'success', 'project', %s, %s::jsonb)",
            (uuid.uuid4(), row[0], str(project_id), '{"method": "dev_tool", "dev_tool": true}'),
        )
        conn.commit()
    return str(row[1])


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "mark-verified":
        print("usage: python -m app.db.dev_tools mark-verified <project_id>", file=sys.stderr)
        return 2
    host = mark_verified(uuid.UUID(sys.argv[2]))
    print(f"marked verified: {host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
