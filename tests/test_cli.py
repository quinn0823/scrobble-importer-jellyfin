from __future__ import annotations

from pathlib import Path

import pytest

from scrobble_importer_jellyfin import (
    ConfigError,
    MissingDatabaseError,
    main,
    parse_args,
)


def test_no_flags_defaults_to_check_mode() -> None:
    args = parse_args([])

    assert args.submit is False
    assert args.check is True


def test_explicit_check_mode() -> None:
    assert parse_args(["--check"]).submit is False


def test_submit_mode() -> None:
    args = parse_args(["--submit"])

    assert args.submit is True
    assert args.check is False


def test_check_and_submit_are_mutually_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--check", "--submit"])

    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_missing_configuration_exits_with_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "JELLYFIN_URL",
        "USERNAME",
        "PASSWORD",
        "PLAYBACK_REPORTING_DB",
        "LISTENBRAINZ_USER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = main([], dotenv_path=tmp_path / "missing.env")

    assert exit_code == 2
    assert "JELLYFIN_URL" in capsys.readouterr().err


def test_submit_mode_without_token_exits_with_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin.local")
    monkeypatch.setenv("USERNAME", "user")
    monkeypatch.setenv("PASSWORD", "pw")
    monkeypatch.setenv("PLAYBACK_REPORTING_DB", str(tmp_path / "playback.sqlite"))
    monkeypatch.delenv("LISTENBRAINZ_USER_TOKEN", raising=False)

    exit_code = main(["--submit"], dotenv_path=tmp_path / "missing.env")

    assert exit_code == 2
    assert "LISTENBRAINZ_USER_TOKEN" in capsys.readouterr().err


def test_missing_database_exits_with_code_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin.local")
    monkeypatch.setenv("USERNAME", "user")
    monkeypatch.setenv("PASSWORD", "pw")
    monkeypatch.setenv("PLAYBACK_REPORTING_DB", str(tmp_path / "playback.sqlite"))

    exit_code = main([], dotenv_path=tmp_path / "missing.env")

    assert exit_code == 1
    assert "PLAYBACK_REPORTING_DB not found" in capsys.readouterr().err
    assert not (tmp_path / "playback.sqlite").exists()


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--version"])

    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_known_exception_types_are_exported() -> None:
    assert issubclass(ConfigError, Exception)
    assert issubclass(MissingDatabaseError, Exception)


def test_runtime_error_from_run_exits_with_code_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scrobble_importer_jellyfin as app

    from conftest import make_playback_db

    db_path = make_playback_db(tmp_path / "playback.sqlite", [])
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin.local")
    monkeypatch.setenv("USERNAME", "user")
    monkeypatch.setenv("PASSWORD", "pw")
    monkeypatch.setenv("PLAYBACK_REPORTING_DB", str(db_path))
    monkeypatch.setattr(app, "load_or_create_device_id", lambda: "fake-device-id")

    def failing_run(*args: object, **kwargs: object) -> None:
        raise app.JellyfinError("upstream exploded")

    monkeypatch.setattr(app, "run", failing_run)

    exit_code = main([], dotenv_path=tmp_path / "missing.env")

    assert exit_code == 1
    assert "upstream exploded" in capsys.readouterr().err
