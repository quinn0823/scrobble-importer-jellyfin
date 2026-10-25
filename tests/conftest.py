from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"

PLAYBACK_ACTIVITY_DDL = (
    "CREATE TABLE PlaybackActivity ("
    "DateCreated DATETIME NOT NULL, "
    "UserId TEXT, "
    "ItemId TEXT, "
    "ItemType TEXT, "
    "ItemName TEXT, "
    "PlaybackMethod TEXT, "
    "ClientName TEXT, "
    "DeviceName TEXT, "
    "PlayDuration INT)"
)


def load_sample(name: str) -> Any:
    return json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data)
        else:
            self.text = ""
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("No JSON body")
        return self._json_data


class FakeSession:
    """A requests.Session stand-in driven by a handler callable."""

    def __init__(self, handler: Callable[[str, str, dict[str, Any]], FakeResponse]) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._handler("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._handler("POST", url, kwargs)


def make_playback_db(
    path: Path,
    rows: list[tuple[Any, ...]],
) -> Path:
    """Create a PlaybackActivity table with rows of
    (DateCreated, UserId, ItemId, ItemType, ItemName, PlaybackMethod, ClientName, DeviceName, PlayDuration).
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute(PLAYBACK_ACTIVITY_DDL)
        connection.executemany(
            "INSERT INTO PlaybackActivity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def sample_artists() -> list[dict[str, Any]]:
    return load_sample("response_artists.json")["Items"]


@pytest.fixture
def sample_audio_items() -> list[dict[str, Any]]:
    return load_sample("response_items_audio.json")["Items"]
