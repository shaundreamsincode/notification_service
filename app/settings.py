"""Runtime configuration.

All tunables come from environment variables. The lease invariant from
design D1/D3 is enforced at load time so a misconfigured API or worker
refuses to start.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTIF_", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://localhost/notifications",
        validation_alias="DATABASE_URL",
    )
    template_dir: Path = REPO_ROOT / "templates"

    max_attempts: int = Field(default=5, ge=1)
    backoff_base_seconds: float = Field(default=2.0, gt=0)
    backoff_cap_seconds: float = Field(default=300.0, gt=0)
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    batch_size: int = Field(default=1, ge=1)
    lease_seconds: float = Field(default=60.0, gt=0)
    provider_deadline_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def lease_covers_the_batch(self) -> "Settings":
        # A lease starts at claim time, but rows are sent one at a time, so
        # the last row of a batch must still finish inside the lease.
        worst_case = self.batch_size * self.provider_deadline_seconds
        if worst_case >= self.lease_seconds:
            raise ValueError(
                "batch_size x provider_deadline_seconds "
                f"({self.batch_size} x {self.provider_deadline_seconds} = {worst_case}) "
                f"must be less than lease_seconds ({self.lease_seconds})"
            )
        return self


class ConfigurationError(Exception):
    """Invalid configuration. The message never includes setting values: a
    pydantic ValidationError would echo the inputs, including DATABASE_URL."""


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'settings'}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigurationError(problems) from None
