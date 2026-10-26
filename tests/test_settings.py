import pytest
from pydantic import ValidationError

from app.settings import Settings


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_defaults_load():
    s = make()
    assert s.max_attempts == 5
    assert s.backoff_base_seconds == 2
    assert s.backoff_cap_seconds == 300
    assert s.poll_interval_seconds == 1
    assert s.batch_size == 1
    assert s.lease_seconds == 60
    assert s.provider_deadline_seconds == 15


def test_deadline_longer_than_lease_is_rejected():
    # Spec scenario: provider timeout 60s with lease 30s.
    with pytest.raises(ValidationError, match="lease_seconds"):
        make(provider_deadline_seconds=60, lease_seconds=30)


def test_deadline_equal_to_lease_is_rejected():
    with pytest.raises(ValidationError):
        make(provider_deadline_seconds=30, lease_seconds=30)


def test_large_batch_with_default_deadline_is_rejected():
    with pytest.raises(ValidationError, match="batch_size"):
        make(batch_size=10, provider_deadline_seconds=15, lease_seconds=60)


def test_valid_override_is_accepted():
    s = make(batch_size=3, provider_deadline_seconds=15, lease_seconds=60)
    assert s.batch_size == 3


def test_environment_variables_are_read(monkeypatch):
    monkeypatch.setenv("NOTIF_BATCH_SIZE", "2")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example/db")
    s = make()
    assert s.batch_size == 2
    assert s.database_url == "postgresql+psycopg://example/db"


def test_get_settings_error_never_echoes_setting_values(monkeypatch):
    import pytest

    from app.settings import ConfigurationError, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:hunter2@db/notifications")
    monkeypatch.setenv("NOTIF_BATCH_SIZE", "10")
    try:
        with pytest.raises(ConfigurationError) as info:
            get_settings()
    finally:
        get_settings.cache_clear()
    assert "lease_seconds" in str(info.value)
    assert "hunter2" not in str(info.value) and info.value.__cause__ is None
    assert info.value.__suppress_context__
