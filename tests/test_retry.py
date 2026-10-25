from __future__ import annotations

from scrobble_importer_jellyfin import backoff_delay, retry_after_delay

from conftest import FakeResponse


def test_numeric_retry_after_is_used() -> None:
    assert retry_after_delay(FakeResponse(429, headers={"Retry-After": "7"})) == 7


def test_retry_after_is_capped() -> None:
    assert retry_after_delay(FakeResponse(429, headers={"Retry-After": "3600"})) == 60


def test_missing_retry_after_returns_none() -> None:
    assert retry_after_delay(FakeResponse(429)) is None


def test_negative_retry_after_returns_none() -> None:
    assert retry_after_delay(FakeResponse(429, headers={"Retry-After": "-5"})) is None


def test_nan_retry_after_returns_none() -> None:
    assert retry_after_delay(FakeResponse(429, headers={"Retry-After": "nan"})) is None


def test_infinite_retry_after_returns_none() -> None:
    assert retry_after_delay(FakeResponse(429, headers={"Retry-After": "inf"})) is None


def test_unparseable_retry_after_returns_none() -> None:
    assert retry_after_delay(FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})) is None


def test_backoff_grows_and_then_holds() -> None:
    assert [backoff_delay(attempt) for attempt in range(5)] == [1, 2, 4, 4, 4]
