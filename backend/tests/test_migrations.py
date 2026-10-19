from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from tests.conftest import BACKEND_DIR, TestDatabase

EXPECTED_TABLES = {
    "users",
    "organizations",
    "memberships",
    "projects",
    "auth_sessions",
    "audit_events",
    "scans",
    "evidence",
    "observations",
    "api_checks",
    "api_check_runs",
    "backend_log_entries",
    "diagnosis_runs",
    "repository_connections",
    "repair_plans",
    "check_runs",
    "approvals",
    "repair_applications",
    "verification_runs",
    "issues",
    "widget_installs",
    "extension_tokens",
    "learning_consents",
    "traffic_imports",
    "issue_exports",
    "desk_steps",
    "kb_pages",
    "kb_chunks",
    "form_definitions",
    "handoffs",
}


def _config(pg: TestDatabase) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["url"] = pg.owner_url
    cfg.attributes["app_role"] = pg.app_role
    cfg.attributes["queue_role"] = pg.queue_role
    return cfg


def _tables(pg: TestDatabase) -> set[str]:
    engine = create_engine(pg.owner_url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_on_a_disposable_database(pg: TestDatabase) -> None:
    cfg = _config(pg)
    assert _tables(pg) == EXPECTED_TABLES
    command.downgrade(cfg, "base")
    assert _tables(pg) == set()
    command.upgrade(cfg, "head")
    assert _tables(pg) == EXPECTED_TABLES
