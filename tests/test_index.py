from __future__ import annotations

from copy import deepcopy

from scrobble_importer_jellyfin import (
    TrackEntry,
    build_artist_index,
    build_track_index,
    build_track_key,
)


def test_build_track_key_matches_playback_reporting_format(sample_audio_items: list[dict]) -> None:
    item = next(item for item in sample_audio_items if item["Name"] == "Shake It Off")

    assert (
        build_track_key(item)
        == "Various Artists - Shake It Off (Sing: Original Motion Picture Soundtrack)"
    )


def test_build_track_key_requires_album_artist_name_and_album(sample_audio_items: list[dict]) -> None:
    item = deepcopy(sample_audio_items[0])

    for field in ("AlbumArtist", "Name", "Album"):
        broken = deepcopy(item)
        broken[field] = ""
        assert build_track_key(broken) is None

        broken[field] = None
        assert build_track_key(broken) is None


def test_build_artist_index_maps_jellyfin_ids_to_provider_ids(sample_artists: list[dict]) -> None:
    index = build_artist_index(sample_artists)

    assert len(index) == 5
    adam_lambert_id = next(item["Id"] for item in sample_artists if item["Name"] == "Adam Lambert")
    assert index[adam_lambert_id]["ProviderIds"]["MusicBrainzArtist"] == (
        "e942594a-91cc-427a-8beb-6815c28e9254"
    )


def test_build_track_index_keeps_jellyfin_values(
    sample_audio_items: list[dict], sample_artists: list[dict]
) -> None:
    result = build_track_index(sample_audio_items, build_artist_index(sample_artists))

    assert result.unindexed == 0
    assert result.duplicate_keys == {}
    assert len(result.tracks) == 5

    cinema = result.tracks["Harry Styles - Cinema (Harry’s House)"]
    assert isinstance(cinema, TrackEntry)
    assert cinema.name == "Cinema"
    assert cinema.album == "Harry’s House"
    assert cinema.runtime_ms == 243539
    assert cinema.index_number == 8
    assert cinema.parent_index_number == 1
    assert cinema.artists == ["Harry Styles"]
    assert cinema.provider_ids["MusicBrainzTrack"] == "e0d6b2a9-5328-4750-977a-4a0d355ad197"


def test_artist_mbids_follow_artist_items_order(sample_audio_items: list[dict], sample_artists: list[dict]) -> None:
    artist_index = build_artist_index(sample_artists)
    artist_index["b6554b106990d04ee47b7b10940a065b"] = {
        "ProviderIds": {"MusicBrainzArtist": "816c4b57-b0a7-40cd-a652-45ac9491ca80"}
    }
    artist_index["7c75f187c81abeaf99f612c01a557cb9"] = {
        "ProviderIds": {"MusicBrainzArtist": "2316a4da-365a-4e46-85bf-febc6537cce9"}
    }

    result = build_track_index(sample_audio_items, artist_index)
    shake_it_off = result.tracks[
        "Various Artists - Shake It Off (Sing: Original Motion Picture Soundtrack)"
    ]

    assert shake_it_off.artists == ["Nick Kroll", "Reese Witherspoon"]
    assert shake_it_off.artist_mbids == [
        "816c4b57-b0a7-40cd-a652-45ac9491ca80",
        "2316a4da-365a-4e46-85bf-febc6537cce9",
    ]


def test_unknown_artist_id_is_kept_as_none(sample_audio_items: list[dict], sample_artists: list[dict]) -> None:
    result = build_track_index(sample_audio_items, build_artist_index(sample_artists))

    shake_it_off = result.tracks[
        "Various Artists - Shake It Off (Sing: Original Motion Picture Soundtrack)"
    ]
    assert shake_it_off.artist_mbids == [None, None]


def test_partial_metadata_entry_is_still_indexed(sample_audio_items: list[dict], sample_artists: list[dict]) -> None:
    item = deepcopy(sample_audio_items[0])
    del item["ProviderIds"]["MusicBrainzAlbum"]

    result = build_track_index([item], build_artist_index(sample_artists))

    entry = next(iter(result.tracks.values()))
    assert "MusicBrainzAlbum" not in entry.provider_ids


def test_item_without_artist_items_is_indexed_with_empty_mbids(
    sample_audio_items: list[dict], sample_artists: list[dict]
) -> None:
    item = deepcopy(sample_audio_items[0])
    item["ArtistItems"] = []

    result = build_track_index([item], build_artist_index(sample_artists))

    entry = next(iter(result.tracks.values()))
    assert entry.artist_mbids == []


def test_duplicate_keys_are_removed_from_index(sample_audio_items: list[dict], sample_artists: list[dict]) -> None:
    first = deepcopy(sample_audio_items[0])
    second = deepcopy(sample_audio_items[0])
    second["Id"] = "another-jellyfin-id"

    result = build_track_index([first, second], build_artist_index(sample_artists))

    key = "Harry Styles - Cinema (Harry’s House)"
    assert result.tracks == {}
    assert result.duplicate_keys == {key: [first["Id"], "another-jellyfin-id"]}


def test_duplicates_do_not_affect_other_tracks(sample_audio_items: list[dict], sample_artists: list[dict]) -> None:
    duplicate = deepcopy(sample_audio_items[0])

    result = build_track_index(
        sample_audio_items + [duplicate], build_artist_index(sample_artists)
    )

    assert "Harry Styles - Cinema (Harry’s House)" in result.duplicate_keys
    assert len(result.tracks) == 4
    assert "Harry Styles - Boyfriends (Harry’s House)" in result.tracks


def test_items_without_a_key_are_counted_as_unindexed(sample_audio_items: list[dict], sample_artists: list[dict]) -> None:
    item = deepcopy(sample_audio_items[0])
    item["Album"] = None

    result = build_track_index([item], build_artist_index(sample_artists))

    assert result.unindexed == 1
    assert result.tracks == {}
