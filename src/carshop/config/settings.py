"""Runtime settings. Every baseline value from requirements section 4 lives here.

Values are configuration, not schema: the owner can change any baseline through the
environment without a migration. Secrets are limited to the model API key and SMTP URL.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Env = Literal["dev", "test", "prod"]
# `chrome` is not a mode: a UA with no product token is an impersonation path (F24).
UaMode = Literal["identified", "native"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CARSHOP_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Env = "dev"
    log_level: str = "INFO"
    data_dir: Path = Path("./data")

    # --- secrets (the only two) ---------------------------------------------------
    model_api_key: SecretStr | None = None
    smtp_url: SecretStr | None = None

    # --- schedule (7.10) ---------------------------------------------------------
    schedule_tz: str = "America/Chicago"
    schedule_hour: int = 6
    digest_deadline_hour: int = 7
    digest_deadline_minute: int = 30
    # NoDecode: pydantic-settings JSON-decodes list fields before validators, so an
    # empty CARSHOP_DIGEST_RECIPIENTS= in .env.example would crash Settings().
    digest_recipients: Annotated[list[str], NoDecode] = Field(default_factory=list)
    digest_from: str = "carshop@localhost"

    # --- scope baselines (3, 4, 7) -----------------------------------------------
    new_odometer_max_miles: int = 500
    price_band_min_cents: int = 55_000_00
    price_band_max_cents: int = 120_000_00
    model_year: int = 2026
    vin_year_code: str = "T"
    target_ag_code: str = "23EU"

    # --- fetch and politeness (7.4, v1.3 baselines) --------------------------------
    concurrency_dealers: int = 4  # global cap of dealers in parallel
    per_host_min_pause_s: float = 5.0  # pause between requests to the same host: uniform in
    per_host_max_pause_s: float = 10.0  # [min, max] seconds
    branch_timeout_s: int = 600  # whole dealer branch (listing + detail); timeout -> PARTIAL
    detail_timeout_s: int = 600  # upper bound for detail enrichment inside the branch budget
    run_wall_clock_s: int = 3600  # target for the whole run; exceeding it is reported, not hidden
    page_cap: int = 30
    http_timeout_s: float = 30.0
    browser_timeout_s: float = 45.0
    browser_settle_ms: int = 6000
    retry_backoff_s: tuple[int, int] = (30, 90)
    http_user_agent: str = "carshop/0.1 (+https://github.com/selchandru6/autonomo-car-agent)"
    # Browser identity (doc/adr/0002-browser-identity.md). identified = real OS, HeadlessChrome,
    # product token, From / X-Carshop. native = Playwright's default UA.
    browser_ua_mode: UaMode = "identified"
    browser_product_token: str = "carshop/0.1"  # noqa: S105 - a UA token, not a secret
    contact_email: str | None = None  # sent as From: when set (RFC 9110 §10.1.2)
    browser_channel: str = "chromium"

    # --- registry (7.1, F1, F25) -----------------------------------------------
    locator_state: str = "TX"
    locator_seed_zips: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["76801", "79901", "78520"]
    )
    operator_id: str | None = None  # audit identity; default is the OS user (CLI review)
    locator_radius_miles: int = 500
    registry_refresh_days: int = 30  # monthly snapshot
    discovery_concurrency: int = 4
    # Operator overrides (privileged). None = the packaged carshop/config/registry/overrides.yaml.
    registry_overrides_path: Path | None = None

    # --- artifacts and retention (7.5) -------------------------------------------
    snapshot_retention_days: int = 14

    # --- model (7.9) --------------------------------------------------------------
    model_name: str = "gpt-4o-mini"
    model_run_budget_usd: float = 0.15
    model_month_budget_usd: float = 5.0

    # --- observability ------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None

    # --- web ----------------------------------------------------------------------
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    @field_validator("digest_recipients", "locator_seed_zips", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    # --- derived paths ------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def checkpoint_db_path(self) -> Path:
        return self.data_dir / "checkpoints.db"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def digests_dir(self) -> Path:
        return self.data_dir / "digests"

    @property
    def registry_dir(self) -> Path:
        return self.data_dir / "registry"

    @property
    def registry_path(self) -> Path:
        """The generated registry (locator snapshot + discovery + overrides)."""
        return self.registry_dir / "dealers.generated.yaml"

    @property
    def model_enabled(self) -> bool:
        return self.model_api_key is not None and bool(self.model_api_key.get_secret_value())

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.snapshots_dir,
            self.backups_dir,
            self.digests_dir,
            self.registry_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
