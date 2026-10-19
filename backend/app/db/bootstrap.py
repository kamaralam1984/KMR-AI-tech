"""Creates the restricted application role. Run once per database server: `make db-init`.

Migrations run as the owner role; the API runs as this role, which has no superuser rights and
no BYPASSRLS, so Row-Level Security always applies to it.
"""

import re
import sys

import psycopg
from psycopg import sql

from app.config import get_settings

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _plain_url(url: str) -> str:
    # psycopg's own connect() does not understand the SQLAlchemy "+driver" suffix.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def ensure_app_role(
    owner_url: str, role: str, password: str, *, reset_password: bool = False
) -> str:
    """Returns "created", "password reset" or "exists"."""
    if not _ROLE_NAME.match(role):
        raise ValueError("role must be a lowercase identifier")
    with psycopg.connect(_plain_url(owner_url), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        attrs = sql.SQL("LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
        if not exists:
            conn.execute(
                sql.SQL("CREATE ROLE {} {} PASSWORD {}").format(
                    sql.Identifier(role), attrs, sql.Literal(password)
                )
            )
            return "created"
        if reset_password:
            conn.execute(
                sql.SQL("ALTER ROLE {} {} PASSWORD {}").format(
                    sql.Identifier(role), attrs, sql.Literal(password)
                )
            )
            return "password reset"
        return "exists"


def ensure_queue_role(owner_url: str, role: str) -> str:
    """The BYPASSRLS role that owns the scan-queue functions. It cannot log in."""
    if not _ROLE_NAME.match(role):
        raise ValueError("role must be a lowercase identifier")
    with psycopg.connect(_plain_url(owner_url), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            return "exists"
        conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(role)
            )
        )
        return "created"


def main() -> int:
    settings = get_settings()
    if settings.migration_database_url is None or settings.app_db_password is None:
        print("Set KMR_MIGRATION_DATABASE_URL and KMR_APP_DB_PASSWORD in .env", file=sys.stderr)
        return 1
    result = ensure_app_role(
        settings.migration_database_url.get_secret_value(),
        settings.app_db_user,
        settings.app_db_password.get_secret_value(),
        reset_password="--reset-password" in sys.argv,
    )
    print(f"app role '{settings.app_db_user}': {result}")
    queue = ensure_queue_role(
        settings.migration_database_url.get_secret_value(), settings.queue_db_user
    )
    print(f"queue role '{settings.queue_db_user}': {queue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
