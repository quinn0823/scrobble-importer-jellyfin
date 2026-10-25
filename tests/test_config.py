from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from scrobble_importer_jellyfin import (
    DEFAULT_DEVICE,
    DEFAULT_LISTENBRAINZ_API_URL,
    Config,
    ConfigError,
    load_config,
    parse_date_env,
)


def required_env(**overrides: str) -> dict[str, str]:
    env = {
        "JELLYFIN_URL": "http://jellyfin.local:8096",
        "USERNAME": "jonathan",
        "PASSWORD": "secret",
        "PLAYBACK_REPORTING_DB": "/data/playback_reporting.db",
    }
    env.update(overrides)
    return env


def test_load_config_applies_documented_defaults() -> None:
    config = load_config(required_env(), submit=False)

    assert isinstance(config, Config)
    assert config.jellyfin_url == "http://jellyfin.local:8096"
    assert config.username == "jonathan"
    assert config.password == "secret"
    assert str(config.db_path) == "/data/playback_reporting.db"
    assert config.listenbrainz_api_url == DEFAULT_LISTENBRAINZ_API_URL
    assert config.listenbrainz_token is None
    assert config.device == DEFAULT_DEVICE
    assert config.timezone == ZoneInfo("UTC")
    assert config.start_date is None
    assert config.end_date is None


def test_load_config_rejects_missing_required_variables_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config({}, submit=False)

    message = str(excinfo.value)
    assert "JELLYFIN_URL" in message
    assert "USERNAME" in message
    assert "PASSWORD" in message
    assert "PLAYBACK_REPORTING_DB" in message


def test_load_config_allows_empty_password_for_passwordless_accounts() -> None:
    config = load_config(required_env(PASSWORD=""), submit=False)

    assert config.password == ""


@pytest.mark.parametrize("name", ["JELLYFIN_URL", "USERNAME", "PLAYBACK_REPORTING_DB"])
def test_load_config_rejects_blank_required_value(name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_config(required_env(**{name: "   "}), submit=False)


def test_submit_mode_requires_listenbrainz_token() -> None:
    with pytest.raises(ConfigError, match="LISTENBRAINZ_USER_TOKEN"):
        load_config(required_env(), submit=True)


def test_check_mode_does_not_require_listenbrainz_token() -> None:
    config = load_config(required_env(LISTENBRAINZ_USER_TOKEN=""), submit=False)

    assert config.listenbrainz_token is None


def test_submit_mode_reads_listenbrainz_token() -> None:
    config = load_config(required_env(LISTENBRAINZ_USER_TOKEN="abc"), submit=True)

    assert config.listenbrainz_token == "abc"


def test_listenbrainz_api_url_trailing_slash_is_stripped() -> None:
    config = load_config(required_env(LISTENBRAINZ_API_URL="http://lb.local/api/"), submit=False)

    assert config.listenbrainz_api_url == "http://lb.local/api"


def test_blank_device_and_tz_fall_back_to_defaults() -> None:
    config = load_config(required_env(DEVICE="", TZ=""), submit=False)

    assert config.device == DEFAULT_DEVICE
    assert config.timezone == ZoneInfo("UTC")


def test_tz_is_parsed() -> None:
    config = load_config(required_env(TZ="Asia/Shanghai"), submit=False)

    assert config.timezone == ZoneInfo("Asia/Shanghai")


def test_unknown_tz_is_rejected() -> None:
    with pytest.raises(ConfigError, match="TZ"):
        load_config(required_env(TZ="Mars/Olympus"), submit=False)


def test_dates_are_parsed() -> None:
    config = load_config(required_env(START_DATE="2025-01-01", END_DATE="2025-12-31"), submit=False)

    assert config.start_date.isoformat() == "2025-01-01"
    assert config.end_date.isoformat() == "2025-12-31"


def test_blank_date_means_unset() -> None:
    config = load_config(required_env(START_DATE="", END_DATE="  "), submit=False)

    assert config.start_date is None
    assert config.end_date is None


@pytest.mark.parametrize("name", ["START_DATE", "END_DATE"])
def test_invalid_date_is_rejected(name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_config(required_env(**{name: "2025/01/01"}), submit=False)


def test_start_after_end_is_rejected() -> None:
    with pytest.raises(ConfigError, match="START_DATE"):
        load_config(required_env(START_DATE="2025-02-01", END_DATE="2025-01-01"), submit=False)


def test_parse_date_env_rejects_impossible_calendar_day() -> None:
    with pytest.raises(ConfigError, match="START_DATE"):
        parse_date_env("START_DATE", "2025-02-30")
