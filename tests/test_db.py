from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scrobble_importer_jellyfin import MissingDatabaseError, iter_audio_rows

from conftest import make_playback_db


def row(
    date_created: str = "2025-02-25 00:49:19.9841297",
    item_type: str = "Audio",
    item_name: str = "Artist - Track (Album)",
    client_name: str | None = "Jellyfin Media Player",
    play_duration: int | None = 200,
) -> tuple:
    return (
        date_created,
        "user-id",
        "item-id",
        item_type,
        item_name,
        "DirectPlay",
        client_name,
        "device",
        play_duration,
    )


def collect(db_path: Path) -> list:
    return list(iter_audio_rows(db_path))


def test_only_audio_rows_are_returned(tmp_path: Path) -> None:
    db_path = make_playback_db(
        tmp_path / "playback.sqlite",
        [row(item_type="Movie"), row(item_type="Audio"), row(item_type="Episode")],
    )

    rows = collect(db_path)

    assert [r.item_name for r in rows] == ["Artist - Track (Album)"]


def test_item_type_comparison_is_case_insensitive(tmp_path: Path) -> None:
    db_path = make_playback_db(tmp_path / "playback.sqlite", [row(item_type="audio")])

    assert len(collect(db_path)) == 1


def test_rows_keep_insertion_order(tmp_path: Path) -> None:
    db_path = make_playback_db(
        tmp_path / "playback.sqlite",
        [
            row(date_created="2025-02-25 00:00:01", item_name="A"),
            row(date_created="2025-02-25 00:00:03", item_name="C"),
            row(date_created="2025-02-25 00:00:02", item_name="B"),
        ],
    )

    assert [r.item_name for r in collect(db_path)] == ["A", "C", "B"]


def test_fields_are_read(tmp_path: Path) -> None:
    db_path = make_playback_db(tmp_path / "playback.sqlite", [row()])

    playback = collect(db_path)[0]

    assert playback.date_created == "2025-02-25 00:49:19.9841297"
    assert playback.item_name == "Artist - Track (Album)"
    assert playback.client_name == "Jellyfin Media Player"
    assert playback.play_duration == 200


def test_nullable_fields_are_tolerated(tmp_path: Path) -> None:
    db_path = make_playback_db(
        tmp_path / "playback.sqlite",
        [row(client_name=None, play_duration=None), row(item_name=None)],
    )

    first, second = collect(db_path)

    assert first.client_name is None
    assert first.play_duration is None
    assert second.item_name == ""


def test_missing_database_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"

    with pytest.raises(MissingDatabaseError):
        collect(missing)
    assert not missing.exists()


def test_database_without_playback_activity_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "other.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE Other (Id TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(MissingDatabaseError, match="PlaybackActivity"):
        collect(db_path)


def test_database_is_not_modified(tmp_path: Path) -> None:
    db_path = make_playback_db(tmp_path / "playback.sqlite", [row()])
    before = db_path.read_bytes()

    collect(db_path)

    assert db_path.read_bytes() == before
