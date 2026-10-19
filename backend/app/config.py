from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository-root .env, so compose, backend and docs share one file regardless of the cwd.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Validated configuration. Every value comes from the environment (prefix KMR_)."""

    model_config = SettingsConfigDict(
        env_prefix="KMR_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # SecretStr keeps these out of repr and logs.
    # database_url connects as the restricted app role (subject to Row-Level Security).
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    # Owner role, used only by migrations and test setup. Never used by request handlers.
    migration_database_url: SecretStr | None = None
    # Name of the restricted role that migrations grant privileges to.
    app_db_user: str = "kmr_app"
    # BYPASSRLS role (no login) that owns the scan-queue functions. See migration 0002.
    queue_db_user: str = "kmr_queue"
    # Password for the app role; read only by the bootstrap command.
    app_db_password: SecretStr | None = None

    # Signs CSRF tokens. Required for anything that handles sessions.
    secret_key: SecretStr | None = None

    cookie_secure: bool | None = None  # None = secure everywhere except local and test
    session_idle_minutes: int = Field(default=8 * 60, ge=5)
    session_absolute_days: int = Field(default=7, ge=1)
    login_max_attempts: int = Field(default=5, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    register_max_per_hour: int = Field(default=10, ge=1)

    # Encrypts API-check credentials at rest (a Fernet key: `python -c "from cryptography.fernet
    # import Fernet; print(Fernet.generate_key().decode())"`). Without it, credentials are refused.
    encryption_key: SecretStr | None = None

    # --- AI diagnosis ---
    # "rules" is the built-in deterministic provider (clearly labeled: it is not AI). "anthropic"
    # needs KMR_AI_API_KEY. Model ids, limits and prices are configuration, never hardcoded.
    ai_provider: Literal["rules", "anthropic"] = "rules"
    ai_api_key: SecretStr | None = None
    ai_model: str = "claude-sonnet-5-5"
    ai_api_url: str = "https://api.anthropic.com/v1/messages"
    ai_max_output_tokens: int = Field(default=4096, ge=256, le=16000)
    ai_timeout_seconds: int = Field(default=60, ge=5, le=300)
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    ai_daily_runs_per_org: int = Field(default=20, ge=1)
    # Cost control. Prices are USD per million tokens; 0 means "unknown", which stores no cost.
    ai_input_price_per_mtok: float = Field(default=0.0, ge=0)
    ai_output_price_per_mtok: float = Field(default=0.0, ge=0)
    # Monthly spend ceiling per organization in USD (0 = no ceiling). Needs prices to be set.
    ai_monthly_cost_ceiling_usd: float = Field(default=0.0, ge=0)

    # --- repositories and repairs ---
    # Local git repositories may be connected only from inside these directories (comma list in
    # the environment as JSON, e.g. '["/srv/repos"]'). Empty disables local repositories.
    repo_local_roots: list[str] = Field(default_factory=list)
    repo_max_files_per_repair: int = Field(default=10, ge=1, le=50)
    repo_max_patch_bytes: int = Field(default=200_000, ge=1_000, le=2_000_000)
    repair_plans_per_day_per_org: int = Field(default=30, ge=1)
    # Image the sandboxed checks run in. It must contain `sh`, and whatever the repository's own
    # check command needs; it is started with no network at all.
    check_image: str = "kmr-scan-browser:1.64.0"
    check_timeout_seconds: int = Field(default=120, ge=5, le=900)
    check_memory: str = "512m"
    check_cpus: float = Field(default=1.0, gt=0, le=4)
    check_pids_limit: int = Field(default=256, ge=16, le=2048)
    approval_ttl_hours: int = Field(default=24, ge=1, le=24 * 14)
    check_runs_per_hour: int = Field(default=20, ge=1)
    # GitHub REST base for the GitHub adapter (not exercised against real GitHub here).
    # Creating a GitHub issue is an outward action and needs the GitHub App's Issues write
    # permission, which the repair connection does not otherwise need. Off unless turned on.
    github_issues_enabled: bool = False
    github_api_url: str = "https://api.github.com"

    # --- visitor assistant ---
    # Public address of the web app, used only to build the install snippet shown to owners.
    public_base_url: str = "http://localhost:3000"
    # Signs and checks the review sign-offs that gate `production_ready` for the learning module.
    signoff_secret: SecretStr | None = None
    desk_chat_daily_per_org: int = Field(default=200, ge=1)
    desk_requests_per_minute: int = Field(default=20, ge=1)
    ext_requests_per_minute: int = Field(default=30, ge=1)
    ext_ingests_per_hour: int = Field(default=60, ge=1)
    assistant_requests_per_minute_per_install: int = Field(default=120, ge=1)
    assistant_requests_per_minute_per_visitor: int = Field(default=30, ge=1)
    assistant_handoffs_per_hour_per_visitor: int = Field(default=3, ge=1)
    assistant_index_per_hour: int = Field(default=20, ge=1)

    # --- scanning ---
    scan_browser_mode: Literal["container", "local"] = "container"
    scan_browser_ws: str = "ws://172.31.250.10:3000"
    scan_proxy_url: str = "http://172.31.250.11:3128"
    scan_local_chrome_path: str | None = None  # only for scan_browser_mode="local"
    # Other engines. Container mode: the websocket of that engine's server (docker/scanner-browser).
    # Local mode (tests and development): the browser binary. Both are needed only if the engine is
    # listed in scan_engines_available, and an engine should be listed only after
    # tests/test_scanner_engines.py passes for it on that deployment.
    scan_browser_ws_firefox: str | None = None
    scan_local_firefox_path: str | None = None
    scan_browser_ws_webkit: str | None = None
    scan_local_webkit_path: str | None = None
    scan_timeout_seconds: int = Field(default=180, ge=10, le=900)
    scan_page_timeout_seconds: int = Field(default=25, ge=5, le=120)
    scan_max_requests: int = Field(default=600, ge=10, le=5000)
    scan_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1024 * 1024)
    scan_max_pages_cap: int = Field(default=50, ge=1, le=50)
    # Engines the browser service can provide. Only Chromium is deployed today.
    scan_engines_available: list[str] = Field(default_factory=lambda: ["chromium"])
    scan_daily_quota_per_org: int = Field(default=20, ge=1)
    verification_checks_per_hour: int = Field(default=10, ge=1)
    api_check_runs_per_hour: int = Field(default=30, ge=1)
    api_check_timeout_seconds: int = Field(default=10, ge=1, le=60)
    # Lets the scanner reach private addresses so tests can use a local fixture site. Honored
    # only when app_env is "test"; anything else refuses to start (see validate_scan_settings).
    scan_allow_private_targets: bool = False
    scan_allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])
    evidence_dir: Path = Path(__file__).resolve().parents[2] / ".data" / "evidence"

    @property
    def cookie_secure_effective(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env not in ("local", "test")

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"


def validate_scan_settings(settings: Settings) -> None:
    """Refuses configurations that would weaken scan safety outside tests."""
    if settings.scan_allow_private_targets and settings.app_env != "test":
        raise RuntimeError("KMR_SCAN_ALLOW_PRIVATE_TARGETS is only allowed when KMR_APP_ENV=test")
    if sorted(settings.scan_allowed_ports) != [80, 443] and settings.app_env != "test":
        raise RuntimeError(
            "KMR_SCAN_ALLOWED_PORTS may only differ from 80,443 when KMR_APP_ENV=test"
        )
    if settings.scan_browser_mode == "local" and settings.app_env not in ("local", "test"):
        raise RuntimeError("KMR_SCAN_BROWSER_MODE=local is only allowed in local/test")
    for engine in settings.scan_engines_available:
        if engine not in ("chromium", "firefox", "webkit"):
            raise RuntimeError(f"KMR_SCAN_ENGINES_AVAILABLE has an unknown engine: {engine}")
        if engine == "chromium":
            continue
        needed = (
            getattr(settings, f"scan_local_{engine}_path")
            if settings.scan_browser_mode == "local"
            else getattr(settings, f"scan_browser_ws_{engine}")
        )
        if not needed:
            raise RuntimeError(
                f"{engine} is listed in KMR_SCAN_ENGINES_AVAILABLE but its "
                f"{'binary path' if settings.scan_browser_mode == 'local' else 'websocket'} "
                "is not configured."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
