from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from zoneinfo import ZoneInfo

from scrobble_importer_jellyfin import Config, Stats, run

from conftest import FakeResponse, FakeSession, load_sample, make_playback_db


@dataclass
class RecordingListenBrainzClient:
    batches: list[list[dict]] = field(default_factory=list)

    def submit_batch(self, listens: list[dict]) -> None:
        self.batches.append([dict(listen) for listen in listens])


class ExplodingListenBrainzClient:
    def submit_batch(self, listens: list[dict]) -> None:
        raise AssertionError("ListenBrainz must not be called in check mode")


def make_config(db_path: Path, **overrides) -> Config:
    values = dict(
        jellyfin_url="http://jellyfin.local:8096",
        username="jonathan",
        password="secret",
        db_path=db_path,
        listenbrainz_api_url="https://lb.example",
        listenbrainz_token="token",
        device="Python",
        timezone=ZoneInfo("UTC"),
        start_date=None,
        end_date=None,
    )
    values.update(overrides)
    return Config(**values)


def audio_item(name: str = "Cinema", album: str = "Harry’s House", album_artist: str = "Harry Styles") -> dict:
    item = load_sample("response_items_audio.json")["Items"][0]
    item = dict(item)
    item["Name"] = name
    item["Album"] = album
    item["AlbumArtist"] = album_artist
    return item


def artists_for(items: list[dict]) -> list[dict]:
    artists = {}
    for item in items:
        for artist_item in item.get("ArtistItems") or []:
            artists[artist_item["Id"]] = {
                "Name": artist_item["Name"],
                "Id": artist_item["Id"],
                "ProviderIds": {"MusicBrainzArtist": "a52450fc-f863-d16e-85bc-0248c2020700"},
            }
    return list(artists.values())


def jellyfin_session(items: list[dict], artists: list[dict] | None = None) -> FakeSession:
    artists = artists if artists is not None else artists_for(items)

    def handler(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url.endswith("/Users/AuthenticateByName"):
            return FakeResponse(200, load_sample("response_users_authenticatebyname.json"))
        if url.endswith("/Artists"):
            return FakeResponse(200, {"Items": artists, "TotalRecordCount": len(artists), "StartIndex": 0})
        if url.endswith("/Items"):
            return FakeResponse(200, {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0})
        raise AssertionError(f"Unexpected request: {method} {url}")

    return FakeSession(handler)


def row(
    date_created: str = "2025-06-06 17:38:07.506126",
    item_name: str = "Harry Styles - Cinema (Harry’s House)",
    play_duration: int = 200,
    client_name: str | None = "Jellyfin Media Player",
    item_type: str = "Audio",
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


def run_scenario(
    tmp_path: Path,
    db_rows: list[tuple],
    items: list[dict],
    *,
    artists: list[dict] | None = None,
    submit: bool = False,
    listenbrainz_client=None,
    config_overrides: dict | None = None,
) -> tuple[Stats, str]:
    db_path = make_playback_db(tmp_path / "playback.sqlite", db_rows)
    output: list[str] = []
    if listenbrainz_client is None:
        listenbrainz_client = RecordingListenBrainzClient() if submit else ExplodingListenBrainzClient()

    stats = run(
        make_config(db_path, **(config_overrides or {})),
        submit=submit,
        jellyfin_client_factory=lambda device_id: _client(jellyfin_session(items, artists)),
        listenbrainz_client=listenbrainz_client,
        device_id="/tmp/fake-device-id",
        out=output.append,
    )
    return stats, "\n".join(output)


def _client(session):
    from scrobble_importer_jellyfin import JellyfinClient

    return JellyfinClient(
        base_url="http://jellyfin.local:8096",
        username="jonathan",
        password="secret",
        device="Python",
        device_id="fake-device-id",
        session=session,
        sleep=lambda _: None,
    )


def test_check_mode_counts_each_category(tmp_path: Path) -> None:
    items = [
        audio_item(),
        audio_item(name="Missing Track Mbid"),
        audio_item(name="Duplicate"),
        audio_item(name="Duplicate"),
    ]
    items[1]["ProviderIds"] = {k: v for k, v in items[1]["ProviderIds"].items() if k != "MusicBrainzTrack"}

    db_rows = [
        row(),  # ready
        row(date_created="2025-06-06 18:00:00", item_name="Harry Styles - Missing Track Mbid (Harry’s House)"),
        row(date_created="2025-06-06 18:10:00", item_name="Harry Styles - Duplicate (Harry’s House)"),
        row(date_created="2025-06-06 18:20:00", item_name="Nobody - Unknown (Nothing)"),
        row(date_created="2025-06-06 18:30:00", play_duration=5),
        row(date_created="2025-06-06 18:40:00", item_type="Movie"),
    ]

    stats, output = run_scenario(tmp_path, db_rows, items)

    assert stats.db_audio_rows == 5
    assert stats.ready_plays == 1
    assert stats.incomplete_plays == 1
    assert stats.duplicate_key_plays == 1
    assert stats.unmatched_plays == 1
    assert stats.filtered_out == 1
    assert stats.submitted_plays == 0
    assert stats.duplicate_keys == 1
    assert "SKIP unmatched: Nobody - Unknown (Nothing)" in output
    assert "SKIP duplicate key: Harry Styles - Duplicate (Harry’s House)" in output
    assert "track_mbid" in output
    assert "check mode" in output


def test_date_range_filters_rows(tmp_path: Path) -> None:
    db_rows = [
        row(date_created="2025-06-05 23:59:59"),
        row(date_created="2025-06-06 00:00:00"),
        row(date_created="2025-06-06 23:59:59"),
        row(date_created="2025-06-07 00:00:00"),
    ]

    stats, _ = run_scenario(
        tmp_path,
        db_rows,
        [audio_item()],
        config_overrides={"start_date": date(2025, 6, 6), "end_date": date(2025, 6, 6)},
    )

    assert stats.out_of_range == 2
    assert stats.ready_plays == 2


def test_invalid_date_is_reported_and_skipped(tmp_path: Path) -> None:
    db_rows = [row(date_created="garbage"), row()]

    stats, output = run_scenario(tmp_path, db_rows, [audio_item()])

    assert stats.invalid_date == 1
    assert stats.ready_plays == 1
    assert "invalid" in output.lower()


def test_tz_applies_to_listened_at(tmp_path: Path) -> None:
    client = RecordingListenBrainzClient()

    run_scenario(
        tmp_path,
        [row(date_created="2025-06-06 17:38:07", play_duration=300)],
        [audio_item()],
        submit=True,
        listenbrainz_client=client,
        config_overrides={"timezone": ZoneInfo("Asia/Shanghai")},
    )

    payload = client.batches[0][0]
    assert payload["listened_at"] == 1749202687


def test_submit_mode_sends_batches_of_1000_and_final_partial(tmp_path: Path) -> None:
    client = RecordingListenBrainzClient()
    db_rows = [row(date_created=f"2025-06-06 00:00:{i % 60:02d}") for i in range(1001)]

    stats, output = run_scenario(
        tmp_path, db_rows, [audio_item()], submit=True, listenbrainz_client=client
    )

    assert [len(batch) for batch in client.batches] == [1000, 1]
    assert stats.submitted_plays == 1001
    assert stats.batches_submitted == 2
    assert "Submitted batch 1" in output


def test_submit_mode_skips_upload_when_nothing_is_ready(tmp_path: Path) -> None:
    client = RecordingListenBrainzClient()

    stats, output = run_scenario(
        tmp_path,
        [row(item_name="Nobody - Unknown (Nothing)")],
        [audio_item()],
        submit=True,
        listenbrainz_client=client,
    )

    assert client.batches == []
    assert stats.submitted_plays == 0
    assert "Nothing to submit" in output


def test_submitted_payload_matches_documented_shape(tmp_path: Path) -> None:
    client = RecordingListenBrainzClient()

    run_scenario(
        tmp_path,
        [row(date_created="2025-06-06 17:38:07", play_duration=300)],
        [audio_item()],
        submit=True,
        listenbrainz_client=client,
    )

    payload = client.batches[0][0]
    assert payload["listened_at"] == 1749231487
    metadata = payload["track_metadata"]
    assert metadata["artist_name"] == "Harry Styles"
    assert metadata["release_name"] == "Harry’s House"
    assert metadata["track_name"] == "Cinema"
    info = metadata["additional_info"]
    assert info["artist_mbids"] == ["a52450fc-f863-d16e-85bc-0248c2020700"]
    assert info["tracknumber"] == 8
    assert info["discnumber"] == 1
    assert info["media_player"] == "Jellyfin Media Player"
    assert info["submission_client"] == "Scrobble Importer Jellyfin"
    assert info["submission_client_version"] == "0.1.0"
    assert info["duration_ms"] == 243539
    assert info["duration_played"] == 300


def test_empty_audio_table_is_not_an_error(tmp_path: Path) -> None:
    stats, output = run_scenario(tmp_path, [row(item_type="Movie")], [audio_item()])

    assert stats.db_audio_rows == 0
    assert "No Audio" in output


class FailingOnSecondBatchListenBrainzClient:
    def __init__(self) -> None:
        from scrobble_importer_jellyfin import ListenBrainzError

        self.error = ListenBrainzError
        self.batches: list[list[dict]] = []

    def submit_batch(self, listens: list[dict]) -> None:
        if len(self.batches) == 1:
            raise self.error("HTTP 503: service unavailable")
        self.batches.append([dict(listen) for listen in listens])


def test_submission_failure_aborts_after_earlier_batches(tmp_path: Path) -> None:
    import pytest

    from scrobble_importer_jellyfin import ListenBrainzError

    client = FailingOnSecondBatchListenBrainzClient()
    db_path = make_playback_db(
        tmp_path / "playback.sqlite", [row(date_created=f"2025-06-06 00:00:{i % 60:02d}") for i in range(1500)]
    )
    output: list[str] = []

    with pytest.raises(ListenBrainzError):
        run(
            make_config(db_path),
            submit=True,
            jellyfin_client_factory=lambda _device_id: _client(jellyfin_session([audio_item()])),
            listenbrainz_client=client,
            device_id="fake-device-id",
            out=output.append,
        )

    assert [len(batch) for batch in client.batches] == [1000]
    assert sum("Submitted batch 1" in line for line in output) == 1
    assert not any("Submitted batch 2" in line for line in output)
