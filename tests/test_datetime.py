from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from scrobble_importer_jellyfin import in_date_range, parse_db_datetime, to_unix_timestamp


def test_parse_seven_fraction_digits_truncates_to_microseconds() -> None:
    parsed = parse_db_datetime("2026-05-31 00:49:00.6924293")

    assert parsed == datetime(2026, 5, 31, 0, 49, 0, 692429)


def test_parse_six_fraction_digits() -> None:
    parsed = parse_db_datetime("2025-06-06 17:38:07.506126")

    assert parsed == datetime(2025, 6, 6, 17, 38, 7, 506126)


def test_parse_two_fraction_digits() -> None:
    parsed = parse_db_datetime("2025-06-06 17:38:07.62")

    assert parsed == datetime(2025, 6, 6, 17, 38, 7, 620000)


def test_parse_without_fraction() -> None:
    parsed = parse_db_datetime("2025-06-06 17:38:07")

    assert parsed == datetime(2025, 6, 6, 17, 38, 7)


def test_parse_iso_t_separator_and_zulu_suffix() -> None:
    parsed = parse_db_datetime("2025-06-06T17:38:07.5Z")

    assert parsed == datetime(2025, 6, 6, 17, 38, 7, 500000)


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_db_datetime("not a datetime")


def test_to_unix_timestamp_uses_utc() -> None:
    naive = datetime(2025, 6, 6, 17, 38, 7)

    expected = calendar.timegm((2025, 6, 6, 17, 38, 7))
    assert to_unix_timestamp(naive, ZoneInfo("UTC")) == expected


def test_to_unix_timestamp_applies_configured_zone() -> None:
    naive = datetime(2025, 6, 6, 17, 38, 7)

    expected = calendar.timegm((2025, 6, 6, 9, 38, 7))
    assert to_unix_timestamp(naive, ZoneInfo("Asia/Shanghai")) == expected


def test_to_unix_timestamp_with_utc_constant() -> None:
    naive = datetime(1970, 1, 1, 0, 0, 0, 500000)

    assert to_unix_timestamp(naive, timezone.utc) == 0


def test_date_range_is_inclusive_on_both_ends() -> None:
    start = date(2025, 6, 6)
    end = date(2025, 6, 8)

    assert in_date_range(date(2025, 6, 6), start, end)
    assert in_date_range(date(2025, 6, 7), start, end)
    assert in_date_range(date(2025, 6, 8), start, end)
    assert not in_date_range(date(2025, 6, 5), start, end)
    assert not in_date_range(date(2025, 6, 9), start, end)


def test_date_range_with_only_start() -> None:
    start = date(2025, 6, 6)

    assert in_date_range(date(2025, 6, 6), start, None)
    assert in_date_range(date(2099, 1, 1), start, None)
    assert not in_date_range(date(2025, 6, 5), start, None)


def test_date_range_with_only_end() -> None:
    end = date(2025, 6, 8)

    assert in_date_range(date(2025, 6, 8), None, end)
    assert in_date_range(date(1900, 1, 1), None, end)
    assert not in_date_range(date(2025, 6, 9), None, end)


def test_date_range_without_bounds_accepts_everything() -> None:
    assert in_date_range(date(2025, 6, 6), None, None)
