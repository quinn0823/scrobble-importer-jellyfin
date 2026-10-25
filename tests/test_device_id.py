from __future__ import annotations

from pathlib import Path

import pytest

from scrobble_importer_jellyfin import DeviceIdError, load_or_create_device_id


def test_creates_uuid_file_when_missing(tmp_path: Path) -> None:
    path = tmp_path / ".device_id"

    device_id = load_or_create_device_id(path)

    assert len(device_id) == 36
    assert device_id.count("-") == 4
    assert path.read_text(encoding="utf-8").strip() == device_id


def test_reads_existing_value(tmp_path: Path) -> None:
    path = tmp_path / ".device_id"
    path.write_text("11111111-2222-3333-4444-555555555555\n", encoding="utf-8")

    assert load_or_create_device_id(path) == "11111111-2222-3333-4444-555555555555"


def test_regenerates_when_file_is_blank(tmp_path: Path) -> None:
    path = tmp_path / ".device_id"
    path.write_text("  \n", encoding="utf-8")

    device_id = load_or_create_device_id(path)

    assert device_id.strip() == device_id
    assert device_id != ""


def test_write_failure_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "no-such-dir" / ".device_id"

    with pytest.raises(DeviceIdError, match="device_id"):
        load_or_create_device_id(path)
