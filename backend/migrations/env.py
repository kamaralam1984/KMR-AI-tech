import re

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

config = context.config

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _database_url() -> str:
    # Tests pass an explicit URL; otherwise use the owner connection from the environment.
    url = config.attributes.get("url")
    if url:
        return str(url)
    configured = get_settings().migration_database_url
    if configured is None:
        raise RuntimeError("KMR_MIGRATION_DATABASE_URL is not set")
    return configured.get_secret_value()


def _app_role() -> str:
    role = str(config.attributes.get("app_role") or get_settings().app_db_user)
    if not _ROLE_NAME.match(role):
        raise RuntimeError("KMR_APP_DB_USER must be a lowercase identifier")
    config.attributes["app_role"] = role
    return role


def _queue_role() -> str:
    role = str(config.attributes.get("queue_role") or get_settings().queue_db_user)
    if not _ROLE_NAME.match(role):
        raise RuntimeError("KMR_QUEUE_DB_USER must be a lowercase identifier")
    config.attributes["queue_role"] = role
    return role


def run_migrations_online() -> None:
    _app_role()
    _queue_role()
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
