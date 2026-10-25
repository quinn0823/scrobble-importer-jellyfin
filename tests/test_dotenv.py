from __future__ import annotations

import os
from pathlib import Path

import pytest

from scrobble_importer_jellyfin import load_dotenv_default


def test_real_environment_wins_over_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("USERNAME=from_file\nPASSWORD=from_file\n", encoding="utf-8")
    monkeypatch.setenv("USERNAME", "from_env")
    monkeypatch.delenv("PASSWORD", raising=False)

    load_dotenv_default(env_file)

    assert os.environ["USERNAME"] == "from_env"
    assert os.environ["PASSWORD"] == "from_file"


def test_missing_dotenv_file_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USERNAME", raising=False)

    load_dotenv_default(tmp_path / "does-not-exist.env")

    assert "USERNAME" not in os.environ
