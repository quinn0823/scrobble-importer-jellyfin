from __future__ import annotations

from typing import Any

import pytest
import requests

from scrobble_importer_jellyfin import (
    ListenBrainzClient,
    ListenBrainzError,
    build_submit_body,
    chunked,
)

from conftest import FakeResponse, FakeSession

API_URL = "https://lb.example"


def make_client(handler, retries: int = 3, sleeps: list[float] | None = None):
    session = FakeSession(handler)
    client = ListenBrainzClient(
        api_url=API_URL + "/",
        token="token-123",
        session=session,
        retries=retries,
        sleep=(sleeps.append if sleeps is not None else (lambda _: None)),
    )
    return client, session


def ok_response() -> FakeResponse:
    return FakeResponse(200, {"status": "ok", "code": 200})


def test_chunked_splits_at_size() -> None:
    assert [len(chunk) for chunk in chunked(range(2500), 1000)] == [1000, 1000, 500]


def test_chunked_empty_sequence_yields_nothing() -> None:
    assert list(chunked([], 1000)) == []


def test_build_submit_body() -> None:
    listens = [{"listened_at": 1}, {"listened_at": 2}]

    assert build_submit_body(listens) == {"listen_type": "import", "payload": listens}


def test_submit_batch_posts_expected_request() -> None:
    client, session = make_client(lambda method, url, kwargs: ok_response())
    listens = [{"listened_at": 1, "track_metadata": {}}]

    client.submit_batch(listens)

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"{API_URL}/1/submit-listens"
    assert kwargs["headers"] == {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Token token-123",
    }
    assert kwargs["json"] == {"listen_type": "import", "payload": listens}


def test_200_with_empty_body_is_accepted() -> None:
    client, _ = make_client(lambda method, url, kwargs: FakeResponse(200, text=""))

    client.submit_batch([{"listened_at": 1}])


def test_200_with_non_ok_status_is_rejected() -> None:
    client, _ = make_client(lambda method, url, kwargs: FakeResponse(200, {"status": "error"}))

    with pytest.raises(ListenBrainzError, match="status"):
        client.submit_batch([{"listened_at": 1}])


@pytest.mark.parametrize(
    ("status", "expected"),
    [(400, "400"), (401, "token")],
)
def test_client_errors_are_fatal_without_retry(status: int, expected: str) -> None:
    attempts = []

    def handler(method, url, kwargs):
        attempts.append(url)
        return FakeResponse(status, {"error": "nope"})

    client, _ = make_client(handler, retries=3)

    with pytest.raises(ListenBrainzError, match=expected):
        client.submit_batch([{"listened_at": 1}])
    assert len(attempts) == 1


def test_rate_limit_is_retried_with_retry_after() -> None:
    attempts = []
    sleeps: list[float] = []

    def handler(method, url, kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            return FakeResponse(429, {}, headers={"Retry-After": "11"})
        return ok_response()

    client, _ = make_client(handler, retries=3, sleeps=sleeps)

    client.submit_batch([{"listened_at": 1}])

    assert len(attempts) == 2
    assert sleeps == [11]


def test_server_errors_are_retried_then_reported() -> None:
    attempts = []

    def handler(method, url, kwargs):
        attempts.append(url)
        return FakeResponse(503, {"error": "down"})

    client, _ = make_client(handler, retries=2)

    with pytest.raises(ListenBrainzError, match="503"):
        client.submit_batch([{"listened_at": 1}])
    assert len(attempts) == 1 + 2


def test_connection_errors_are_retried_then_reported() -> None:
    attempts = []

    def handler(method, url, kwargs):
        attempts.append(url)
        raise requests.ConnectionError("refused")

    client, _ = make_client(handler, retries=2)

    with pytest.raises(ListenBrainzError, match="Connection error"):
        client.submit_batch([{"listened_at": 1}])
    assert len(attempts) == 1 + 2


def test_non_json_error_body_does_not_crash() -> None:
    client, _ = make_client(lambda method, url, kwargs: FakeResponse(400, text="Bad Request"))

    with pytest.raises(ListenBrainzError, match="400"):
        client.submit_batch([{"listened_at": 1}])
