from __future__ import annotations

from typing import Any, Callable

import pytest
import requests

from scrobble_importer_jellyfin import JellyfinClient, JellyfinError

from conftest import FakeResponse, FakeSession, load_sample

BASE_URL = "http://jellyfin.local:8096"
DEVICE_ID = "11111111-2222-3333-4444-555555555555"
EXPECTED_AUTH = (
    'MediaBrowser Client="Scrobble Importer Jellyfin", Device="Python", '
    f'DeviceId="{DEVICE_ID}", Version="0.1.0"'
)

Handler = Callable[[str, str, dict[str, Any]], FakeResponse]


def auth_response() -> FakeResponse:
    return FakeResponse(200, load_sample("response_users_authenticatebyname.json"))


def routed(api_handler: Handler) -> Handler:
    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/Users/AuthenticateByName"):
            return auth_response()
        return api_handler(method, url, kwargs)

    return handler


def make_client(
    handler: Handler, retries: int = 3, sleeps: list[float] | None = None
) -> tuple[JellyfinClient, FakeSession]:
    session = FakeSession(handler)
    client = JellyfinClient(
        base_url=BASE_URL + "/",
        username="jonathan",
        password="secret",
        device="Python",
        device_id=DEVICE_ID,
        session=session,
        retries=retries,
        sleep=(sleeps.append if sleeps is not None else (lambda _: None)),
    )
    return client, session


def test_login_posts_exact_headers_and_body() -> None:
    client, session = make_client(lambda method, url, kwargs: auth_response())

    token, user_id = client.login()

    assert token == "32995775e6884610a233715d08fd02d6"
    assert user_id == "b58fa2d2e44d4c7e9e6e74d83a1b85fc"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"{BASE_URL}/Users/AuthenticateByName"
    assert kwargs["headers"]["Authorization"] == EXPECTED_AUTH
    assert kwargs["headers"]["accept"] == "application/json"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"] == {"Username": "jonathan", "Pw": "secret"}


def test_authenticated_requests_append_token_to_header() -> None:
    client, session = make_client(routed(lambda m, u, k: FakeResponse(200, {"Items": [], "TotalRecordCount": 0})))
    token, user_id = client.login()

    client.fetch_all_artists(user_id)

    _, _, kwargs = session.calls[-1]
    assert kwargs["headers"]["Authorization"] == f'{EXPECTED_AUTH}, Token="{token}"'


def test_login_rejects_invalid_credentials_without_retry() -> None:
    client, session = make_client(lambda method, url, kwargs: FakeResponse(401, {"error": "bad"}))

    with pytest.raises(JellyfinError, match="Authentication failed"):
        client.login()
    assert len(session.calls) == 1


def test_login_rejects_unknown_user_without_retry() -> None:
    client, session = make_client(lambda method, url, kwargs: FakeResponse(403, {"error": "no access"}))

    with pytest.raises(JellyfinError) as excinfo:
        client.login()
    assert excinfo.value.status_code == 403
    assert len(session.calls) == 1


def test_login_requires_access_token_and_user_id() -> None:
    client, _ = make_client(lambda m, u, k: FakeResponse(200, {"AccessToken": "", "User": {}}))

    with pytest.raises(JellyfinError, match="AccessToken"):
        client.login()


def test_login_non_json_success_body_is_reported() -> None:
    client, _ = make_client(lambda m, u, k: FakeResponse(200, text="ok"))

    with pytest.raises(JellyfinError, match="AccessToken"):
        client.login()


def test_pagination_collects_every_page_with_expected_parameters(sample_artists: list[dict]) -> None:
    pages = [
        FakeResponse(200, {"Items": sample_artists[:2], "TotalRecordCount": 5, "StartIndex": 0}),
        FakeResponse(200, {"Items": sample_artists[2:4], "TotalRecordCount": 5, "StartIndex": 2}),
        FakeResponse(200, {"Items": sample_artists[4:], "TotalRecordCount": 5, "StartIndex": 4}),
    ]
    served = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        served.append(kwargs["params"])
        return pages[len(served) - 1]

    client, session = make_client(routed(handler))
    client.login()

    artists = client.fetch_all_artists("user-1")

    assert artists == sample_artists
    assert [params["startIndex"] for params in served] == [0, 2, 4]
    for params in served:
        assert params["fields"] == "ProviderIds"
        assert params["userId"] == "user-1"
        assert params["limit"] == 500
    assert all(call[1] == f"{BASE_URL}/Artists" for call in session.calls[1:])


def test_audio_items_request_uses_required_query_parameters(sample_audio_items: list[dict]) -> None:
    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        return FakeResponse(200, {"Items": sample_audio_items, "TotalRecordCount": 5, "StartIndex": 0})

    client, session = make_client(routed(handler))
    client.login()

    items = client.fetch_all_audio_items("user-1")

    assert items == sample_audio_items
    _, url, kwargs = session.calls[-1]
    assert url == f"{BASE_URL}/Items"
    assert kwargs["params"]["userId"] == "user-1"
    assert kwargs["params"]["recursive"] == "true"
    assert kwargs["params"]["fields"] == "ProviderIds"
    assert kwargs["params"]["includeItemTypes"] == "Audio"


def test_pagination_stops_when_total_is_reached_even_if_counts_are_odd() -> None:
    served = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        served.append(kwargs["params"]["startIndex"])
        return FakeResponse(200, {"Items": [{"Id": f"a{len(served)}"}], "TotalRecordCount": 1})

    client, _ = make_client(routed(handler))
    client.login()

    result = client.fetch_all_artists("user-1")

    assert result == [{"Id": "a1"}]
    assert served == [0]


def test_empty_library_returns_no_items() -> None:
    client, _ = make_client(routed(lambda m, u, k: FakeResponse(200, {"Items": [], "TotalRecordCount": 0})))
    client.login()

    assert client.fetch_all_audio_items("user-1") == []


def test_server_errors_are_retried_then_reported() -> None:
    attempts = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        attempts.append(url)
        return FakeResponse(503, {"error": "down"})

    client, _ = make_client(routed(handler), retries=3)
    client.login()

    with pytest.raises(JellyfinError, match="503"):
        client.fetch_all_artists("user-1")
    assert len(attempts) == 1 + 3


def test_server_error_recovers_on_retry() -> None:
    attempts = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        attempts.append(url)
        if len(attempts) == 1:
            return FakeResponse(500, {})
        return FakeResponse(200, {"Items": [{"Id": "x"}], "TotalRecordCount": 1})

    client, _ = make_client(routed(handler), retries=3)
    client.login()

    assert client.fetch_all_artists("user-1") == [{"Id": "x"}]


def test_retry_after_header_is_honored() -> None:
    sleeps: list[float] = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if method == "GET":
            return FakeResponse(429, {}, headers={"Retry-After": "7"})
        return auth_response()

    client, _ = make_client(routed(handler), retries=1, sleeps=sleeps)
    client.login()

    with pytest.raises(JellyfinError, match="429"):
        client.fetch_all_artists("user-1")
    assert sleeps == [7]


def test_connection_errors_are_retried_then_reported() -> None:
    attempts = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        attempts.append(url)
        raise requests.ConnectionError("refused")

    client, _ = make_client(handler, retries=2)

    with pytest.raises(JellyfinError, match="Connection error"):
        client.login()
    assert len(attempts) == 1 + 2


def test_token_rejected_on_later_request_is_not_retried() -> None:
    attempts = []

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        attempts.append(url)
        if url.endswith("/Users/AuthenticateByName"):
            return auth_response()
        return FakeResponse(401, {})

    client, _ = make_client(handler)

    with pytest.raises(JellyfinError, match="rejected"):
        client.login()
        client.fetch_all_artists("user-1")
    assert len(attempts) == 2


def test_non_json_error_body_does_not_crash() -> None:
    client, _ = make_client(lambda m, u, k: FakeResponse(400, text="Bad Request"))

    with pytest.raises(JellyfinError, match="400"):
        client.login()
