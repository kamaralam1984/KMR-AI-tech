import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.db.bootstrap import ensure_app_role, ensure_queue_role
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_ENV = BACKEND_DIR.parent / ".env"
TEST_SECRET_KEY = "test-secret-key-" + "x" * 32


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "KMR_APP_ENV",
        "KMR_DATABASE_URL",
        "KMR_MIGRATION_DATABASE_URL",
        "KMR_REDIS_URL",
        "KMR_CORS_ORIGINS",
        "KMR_SECRET_KEY",
        "KMR_COOKIE_SECURE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)  # ignore a developer .env
    monkeypatch.setenv("KMR_APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False so unhandled errors surface as real 500 responses.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------- database fixtures


@dataclass(frozen=True)
class TestDatabase:
    __test__ = False

    owner_url: str  # superuser-ish owner: migrations and seeding only
    app_url: str  # the restricted role the API uses
    app_role: str
    queue_role: str
    database: str


def _swap_database(url: str, database: str) -> str:
    head, _, _ = url.rpartition("/")
    return f"{head}/{database}"


@pytest.fixture(scope="session")
def pg() -> Iterator[TestDatabase]:
    """A disposable database and role, created for this run and dropped afterwards.

    Never touches the developer database. Skipped (visibly) if Postgres is unreachable.
    """
    owner_dev_url = Settings(_env_file=ROOT_ENV).migration_database_url  # type: ignore[call-arg]
    if owner_dev_url is None:
        pytest.skip("Unavailable: KMR_MIGRATION_DATABASE_URL is not set (see .env.example)")
    server_url = owner_dev_url.get_secret_value()
    plain = server_url.replace("postgresql+psycopg://", "postgresql://", 1)

    suffix = secrets.token_hex(4)
    database, role, password = f"kmr_test_{suffix}", f"kmr_t_{suffix}", secrets.token_hex(16)
    queue_role = f"kmr_tq_{suffix}"
    try:
        admin = psycopg.connect(plain, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Unavailable: cannot reach Postgres ({type(exc).__name__}). Run `make up`.")

    owner_url = _swap_database(server_url, database)
    host_part = owner_url.split("@", 1)[1]
    app_url = f"postgresql+psycopg://{role}:{password}@{host_part}"
    try:
        admin.execute(f'CREATE DATABASE "{database}"')
        ensure_app_role(server_url, role, password)
        ensure_queue_role(server_url, queue_role)
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        cfg.attributes["url"] = owner_url
        cfg.attributes["app_role"] = role
        cfg.attributes["queue_role"] = queue_role
        command.upgrade(cfg, "head")
        yield TestDatabase(owner_url, app_url, role, queue_role, database)
    finally:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (database,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        admin.execute(f'DROP ROLE IF EXISTS "{queue_role}"')
        admin.close()


@pytest.fixture
def owner_engine(pg: TestDatabase) -> Iterator[Engine]:
    """Owner connection, used to seed data and to inspect state behind the app's back."""
    engine = create_sync_engine(pg.owner_url)
    with engine.begin() as conn:
        # Every table except Alembic's bookkeeping, so new phases need no edit here.
        tables = conn.exec_driver_sql(
            "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        ).scalar()
        conn.exec_driver_sql(f"TRUNCATE {tables} CASCADE")  # noqa: S608 - names from pg_tables
    yield engine
    engine.dispose()


@pytest.fixture
def app_sync_engine(pg: TestDatabase) -> Iterator[Engine]:
    """Sync connection as the restricted app role, for testing RLS policies directly."""
    engine = create_sync_engine(pg.app_url, pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


@dataclass(frozen=True)
class Seeded:
    user_a: uuid.UUID
    user_b: uuid.UUID
    org_a: uuid.UUID
    org_b: uuid.UUID
    project_a: uuid.UUID
    project_b: uuid.UUID


@pytest.fixture
def seeded(owner_engine: Engine) -> Seeded:
    """Two tenants, each with an owner and one project."""
    ids = {name: uuid.uuid4() for name in ("ua", "ub", "oa", "ob", "pa", "pb")}
    with owner_engine.begin() as c:
        for key, email in (("ua", "a@example.test"), ("ub", "b@example.test")):
            c.exec_driver_sql(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'x')",
                (ids[key], email),
            )
        for okey, ukey, pkey, name in (
            ("oa", "ua", "pa", "Org A"),
            ("ob", "ub", "pb", "Org B"),
        ):
            c.exec_driver_sql(
                "INSERT INTO organizations (id, name, created_by) VALUES (%s, %s, %s)",
                (ids[okey], name, ids[ukey]),
            )
            c.exec_driver_sql(
                "INSERT INTO memberships (org_id, user_id, role) VALUES (%s, %s, 'owner')",
                (ids[okey], ids[ukey]),
            )
            c.exec_driver_sql(
                "INSERT INTO projects (id, org_id, name, created_by) VALUES (%s, %s, %s, %s)",
                (ids[pkey], ids[okey], f"Project of {name}", ids[ukey]),
            )
    return Seeded(ids["ua"], ids["ub"], ids["oa"], ids["ob"], ids["pa"], ids["pb"])


# --------------------------------------------------------------------------- API fixtures


@pytest.fixture
def api_app(
    pg: TestDatabase, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """The real app wired to the disposable database, with its lifespan running."""
    monkeypatch.setenv("KMR_DATABASE_URL", pg.app_url)
    monkeypatch.setenv("KMR_SECRET_KEY", TEST_SECRET_KEY)
    get_settings.cache_clear()
    application = create_app()
    with TestClient(application, raise_server_exceptions=False) as shared:
        application.state.test_client = shared
        yield application


@pytest.fixture
def new_api(api_app: FastAPI):  # type: ignore[no-untyped-def]
    """Factory for independent API users, each with its own cookie jar.

    All users share one TestClient: it keeps a single event loop alive, which the async database
    pool needs. Cookies are swapped in and out per request instead.
    """
    from tests.helpers import Api

    def make() -> Api:
        return Api(api_app.state.test_client)

    return make


@pytest.fixture
def seeded_scans(owner_engine: Engine, seeded: Seeded) -> dict[str, uuid.UUID]:
    """One queued scan per tenant, on top of `seeded`."""
    ids = {"scan_a": uuid.uuid4(), "scan_b": uuid.uuid4()}
    with owner_engine.begin() as c:
        for key, org, proj, user, host in (
            ("scan_a", seeded.org_a, seeded.project_a, seeded.user_a, "a.example.com"),
            ("scan_b", seeded.org_b, seeded.project_b, seeded.user_b, "b.example.com"),
        ):
            c.exec_driver_sql(
                "INSERT INTO scans (id, org_id, project_id, requested_by, site_url, allowed_host, "
                "limits) VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb)",
                (ids[key], org, proj, user, f"https://{host}/", host),
            )
    return ids


@pytest.fixture
def fake_verifier(api_app: FastAPI):  # type: ignore[no-untyped-def]
    from tests.fakes import FakeVerifier

    verifier = FakeVerifier()
    api_app.state.ownership_verifier = verifier
    return verifier


@pytest.fixture
def fake_queue(api_app: FastAPI):  # type: ignore[no-untyped-def]
    from tests.fakes import FakeQueue

    queue = FakeQueue()
    api_app.state.scan_queue = queue
    return queue


@pytest.fixture
def fake_transport(api_app: FastAPI):  # type: ignore[no-untyped-def]
    from tests.fakes import FakeTransport

    transport = FakeTransport()
    api_app.state.api_transport = transport
    return transport


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("KMR_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    return key
