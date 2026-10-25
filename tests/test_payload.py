from __future__ import annotations

from dataclasses import replace

import pytest

from scrobble_importer_jellyfin import (
    PlaybackRow,
    TrackEntry,
    build_payload,
    missing_fields,
    passes_duration_filter,
)


def make_entry(**overrides: object) -> TrackEntry:
    entry = TrackEntry(
        name="Shake It Off",
        album="Sing: Original Motion Picture Soundtrack",
        runtime_ms=120607,
        index_number=13,
        parent_index_number=1,
        provider_ids={
            "MusicBrainzArtist": "816c4b57-b0a7-40cd-a652-45ac9491ca80",
            "MusicBrainzAlbumArtist": "89ad4ac3-39f7-470e-963a-56509c546377",
            "MusicBrainzAlbum": "0a8e5eb0-70b7-4b89-9a93-926e7afe332f",
            "MusicBrainzReleaseGroup": "143792fa-0a91-4a9b-82e3-45b6c592c933",
            "MusicBrainzTrack": "6ad2831f-6e18-422e-8353-5e33616a3028",
        },
        artists=["Nick Kroll", "Reese Witherspoon"],
        artist_mbids=[
            "816c4b57-b0a7-40cd-a652-45ac9491ca80",
            "2316a4da-365a-4e46-85bf-febc6537cce9",
        ],
    )
    return replace(entry, **overrides)


def make_row(**overrides: object) -> PlaybackRow:
    row = PlaybackRow(
        date_created="2025-02-25 00:49:19.9841297",
        item_name="Various Artists - Shake It Off (Sing: Original Motion Picture Soundtrack)",
        client_name="Jellyfin Media Player",
        play_duration=147,
    )
    return replace(row, **overrides)


@pytest.mark.parametrize(
    ("play_duration", "runtime_ms", "expected"),
    [
        (240, None, True),
        (240, 120607, True),
        (241, 0, True),
        (239, 600000, False),
        (60, 120000, True),  # 60 * 1000 >= 120000 / 2
        (59, 120000, False),
        (10, 240000, False),
        (0, 1000, False),
        (-5, 1000, False),
        (None, 1000, False),
    ],
)
def test_passes_duration_filter(play_duration, runtime_ms, expected) -> None:
    assert passes_duration_filter(play_duration, runtime_ms) is expected


def test_complete_entry_has_no_missing_fields() -> None:
    assert missing_fields(make_entry(), make_row()) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"artists": []}, ["artist_name", "artist_mbids"]),
        ({"artists": ["Nick Kroll", " "]}, ["artist_name"]),
        ({"artist_mbids": [None, "2316a4da-365a-4e46-85bf-febc6537cce9"]}, ["artist_mbids"]),
        ({"artist_mbids": []}, ["artist_mbids"]),
        ({"album": ""}, ["release_name"]),
        ({"name": None}, ["track_name"]),
        ({"index_number": None}, ["tracknumber"]),
        ({"parent_index_number": None}, ["discnumber"]),
        ({"runtime_ms": None}, ["duration_ms"]),
    ],
)
def test_missing_entry_fields(overrides: dict, expected: list[str]) -> None:
    assert missing_fields(make_entry(**overrides), make_row()) == expected


@pytest.mark.parametrize(
    ("row_overrides", "expected"),
    [
        ({"client_name": ""}, "media_player"),
        ({"client_name": None}, "media_player"),
        ({"play_duration": 0}, "duration_played"),
        ({"play_duration": None}, "duration_played"),
    ],
)
def test_missing_playback_row_fields(row_overrides: dict, expected: str) -> None:
    assert missing_fields(make_entry(), make_row(**row_overrides)) == [expected]


@pytest.mark.parametrize(
    "provider_key",
    [
        "MusicBrainzAlbum",
        "MusicBrainzReleaseGroup",
        "MusicBrainzTrack",
    ],
)
def test_missing_provider_id(provider_key: str) -> None:
    entry = make_entry()
    broken = make_entry(provider_ids={k: v for k, v in entry.provider_ids.items() if k != provider_key})

    expected = {
        "MusicBrainzAlbum": "release_mbid",
        "MusicBrainzReleaseGroup": "release_group_mbid",
        "MusicBrainzTrack": "track_mbid",
    }[provider_key]
    assert missing_fields(broken, make_row()) == [expected]


def test_missing_fields_are_reported_in_canonical_order() -> None:
    entry = make_entry(
        album=None,
        provider_ids={},
        artist_mbids=[None, None],
    )

    assert missing_fields(entry, make_row()) == [
        "artist_mbids",
        "release_name",
        "release_group_mbid",
        "release_mbid",
        "track_mbid",
    ]


def test_build_payload_matches_documented_example() -> None:
    payload = build_payload(make_entry(), make_row(), 1680000000)

    assert payload == {
        "listened_at": 1680000000,
        "track_metadata": {
            "artist_name": "Nick Kroll, Reese Witherspoon",
            "release_name": "Sing: Original Motion Picture Soundtrack",
            "track_name": "Shake It Off",
            "additional_info": {
                "artist_mbids": [
                    "816c4b57-b0a7-40cd-a652-45ac9491ca80",
                    "2316a4da-365a-4e46-85bf-febc6537cce9",
                ],
                "release_group_mbid": "143792fa-0a91-4a9b-82e3-45b6c592c933",
                "release_mbid": "0a8e5eb0-70b7-4b89-9a93-926e7afe332f",
                "track_mbid": "6ad2831f-6e18-422e-8353-5e33616a3028",
                "tracknumber": 13,
                "discnumber": 1,
                "media_player": "Jellyfin Media Player",
                "submission_client": "Scrobble Importer Jellyfin",
                "submission_client_version": "0.1.0",
                "duration_ms": 120607,
                "duration_played": 147,
            },
        },
    }
