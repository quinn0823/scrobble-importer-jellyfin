from __future__ import annotations

import math
import os
import re
import sqlite3
import sys
import time
import uuid
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

MEDIA_BROWSER_CLIENT = "Scrobble Importer Jellyfin"
VERSION = "0.1.0"
DEFAULT_DEVICE = "Python"
DEFAULT_TZ = "UTC"
DEFAULT_LISTENBRAINZ_API_URL = "https://api.listenbrainz.org"
DEFAULT_DOTENV_PATH = Path(".env")
DATE_FORMAT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DATE_PART_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def parse_db_datetime(raw: str) -> datetime:
    """Parse an SQLite DATETIME string such as ``2025-02-25 00:49:19.9841297``.

    Returns a naive datetime; fraction digits beyond microseconds are truncated.
    """
    match = _DATE_PART_RE.match(raw.strip())
    if match is None:
        raise ValueError(f"Invalid DateCreated value: {raw!r}")
    date_part, time_part, fraction = match.groups()
    microseconds = int((fraction or "").ljust(6, "0")[:6])
    base = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
    return base + timedelta(microseconds=microseconds)


def to_unix_timestamp(native: datetime, zone: Union[tzinfo, str]) -> int:
    """Interpret a naive datetime in ``zone`` and return whole Unix seconds."""
    if isinstance(zone, str):
        zone = ZoneInfo(zone)
    if native.tzinfo is not None:
        native = native.replace(tzinfo=None)
    return int(native.replace(tzinfo=zone).timestamp())


def in_date_range(value: date, start: Optional[date], end: Optional[date]) -> bool:
    """Inclusive date range check; ``None`` bounds are open-ended."""
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


class ConfigError(Exception):
    """Raised when environment configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    jellyfin_url: str
    username: str
    password: str
    db_path: Path
    listenbrainz_api_url: str
    listenbrainz_token: Optional[str]
    device: str
    timezone: ZoneInfo
    start_date: Optional[date]
    end_date: Optional[date]


def load_dotenv_default(path: Path = DEFAULT_DOTENV_PATH) -> None:
    """Load ``.env`` without overriding variables already present in the environment."""
    load_dotenv(dotenv_path=path, override=False)


def parse_date_env(name: str, raw: Optional[str]) -> Optional[date]:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if not DATE_FORMAT_RE.match(value):
        raise ConfigError(f"{name} must use YYYY-MM-DD format, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ConfigError(f"{name} is not a valid date: {value!r}") from error


def load_config(env: Mapping[str, str], submit: bool) -> Config:
    required = ("JELLYFIN_URL", "USERNAME", "PASSWORD", "PLAYBACK_REPORTING_DB")
    missing = []
    for name in required:
        if name not in env:
            missing.append(name)
        elif name != "PASSWORD" and not env[name].strip():
            missing.append(name)
    if submit and not env.get("LISTENBRAINZ_USER_TOKEN", "").strip():
        missing.append("LISTENBRAINZ_USER_TOKEN")
    if missing:
        raise ConfigError("Missing required environment variables: " + ", ".join(missing))

    tz_name = env.get("TZ", "").strip() or DEFAULT_TZ
    try:
        timezone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as error:
        raise ConfigError(f"Invalid TZ value: {tz_name!r}") from error

    start_date = parse_date_env("START_DATE", env.get("START_DATE"))
    end_date = parse_date_env("END_DATE", env.get("END_DATE"))
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ConfigError("START_DATE must not be later than END_DATE")

    api_url = env.get("LISTENBRAINZ_API_URL", "").strip() or DEFAULT_LISTENBRAINZ_API_URL
    token = env.get("LISTENBRAINZ_USER_TOKEN", "").strip() or None

    return Config(
        jellyfin_url=env["JELLYFIN_URL"].strip().rstrip("/"),
        username=env["USERNAME"].strip(),
        password=env["PASSWORD"],
        db_path=Path(env["PLAYBACK_REPORTING_DB"].strip()),
        listenbrainz_api_url=api_url.rstrip("/"),
        listenbrainz_token=token,
        device=env.get("DEVICE", "").strip() or DEFAULT_DEVICE,
        timezone=timezone,
        start_date=start_date,
        end_date=end_date,
    )


def is_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


@dataclass(frozen=True)
class TrackEntry:
    name: Optional[str]
    album: Optional[str]
    runtime_ms: Optional[int]
    index_number: Optional[int]
    parent_index_number: Optional[int]
    provider_ids: dict[str, str]
    artists: list[str]
    artist_mbids: list[Optional[str]]


@dataclass(frozen=True)
class TrackIndexResult:
    tracks: dict[str, TrackEntry]
    duplicate_keys: dict[str, list[Optional[str]]]
    unindexed: int


def build_artist_index(artists: list[dict]) -> dict[str, dict[str, dict[str, str]]]:
    index = {}
    for artist in artists:
        artist_id = artist.get("Id")
        if not is_present(artist_id):
            continue
        index[str(artist_id)] = {"ProviderIds": dict(artist.get("ProviderIds") or {})}
    return index


def build_track_key(item: Mapping[str, object]) -> Optional[str]:
    album_artist = item.get("AlbumArtist")
    name = item.get("Name")
    album = item.get("Album")
    if not (is_present(album_artist) and is_present(name) and is_present(album)):
        return None
    return f"{album_artist} - {name} ({album})"


def _build_track_entry(
    item: Mapping[str, object], artist_index: Mapping[str, dict[str, dict[str, str]]]
) -> TrackEntry:
    runtime_ticks = item.get("RunTimeTicks")
    runtime_ms = int(runtime_ticks) // 10000 if runtime_ticks is not None else None

    artist_mbids: list[Optional[str]] = []
    for artist_item in item.get("ArtistItems") or []:
        artist = artist_index.get(str(artist_item.get("Id")), {})
        artist_mbids.append(artist.get("ProviderIds", {}).get("MusicBrainzArtist"))

    return TrackEntry(
        name=item.get("Name"),
        album=item.get("Album"),
        runtime_ms=runtime_ms,
        index_number=item.get("IndexNumber"),
        parent_index_number=item.get("ParentIndexNumber"),
        provider_ids=dict(item.get("ProviderIds") or {}),
        artists=list(item.get("Artists") or []),
        artist_mbids=artist_mbids,
    )


def build_track_index(
    items: list[dict], artist_index: Mapping[str, dict[str, dict[str, str]]]
) -> TrackIndexResult:
    grouped: dict[str, list[tuple[Mapping[str, object], TrackEntry]]] = {}
    unindexed = 0
    for item in items:
        key = build_track_key(item)
        if key is None:
            unindexed += 1
            continue
        grouped.setdefault(key, []).append((item, _build_track_entry(item, artist_index)))

    tracks = {}
    duplicate_keys = {}
    for key, entries in grouped.items():
        if len(entries) > 1:
            duplicate_keys[key] = [entry_item.get("Id") for entry_item, _ in entries]
        else:
            tracks[key] = entries[0][1]
    return TrackIndexResult(tracks=tracks, duplicate_keys=duplicate_keys, unindexed=unindexed)


@dataclass(frozen=True)
class PlaybackRow:
    date_created: str
    item_name: str
    client_name: Optional[str]
    play_duration: Optional[int]


PLAY_THRESHOLD_SECONDS = 240


def passes_duration_filter(play_duration: Optional[int], runtime_ms: Optional[int]) -> bool:
    if play_duration is None or play_duration <= 0:
        return False
    if play_duration >= PLAY_THRESHOLD_SECONDS:
        return True
    if runtime_ms is None:
        return False
    return play_duration * 1000 * 2 >= runtime_ms


def missing_fields(entry: TrackEntry, row: PlaybackRow) -> list[str]:
    """Return the payload fields that cannot be built, in canonical order."""
    missing = []

    artists_ok = bool(entry.artists) and all(is_present(artist) for artist in entry.artists)
    if not artists_ok:
        missing.append("artist_name")

    mbids_ok = (
        len(entry.artist_mbids) == len(entry.artists) > 0
        and all(is_present(mbid) for mbid in entry.artist_mbids)
    )
    if not mbids_ok:
        missing.append("artist_mbids")

    if not is_present(entry.album):
        missing.append("release_name")
    if not is_present(entry.name):
        missing.append("track_name")

    provider_requirements = (
        ("MusicBrainzReleaseGroup", "release_group_mbid"),
        ("MusicBrainzAlbum", "release_mbid"),
        ("MusicBrainzTrack", "track_mbid"),
    )
    for provider_key, payload_field in provider_requirements:
        if not is_present(entry.provider_ids.get(provider_key)):
            missing.append(payload_field)

    if entry.index_number is None:
        missing.append("tracknumber")
    if entry.parent_index_number is None:
        missing.append("discnumber")
    if not is_present(row.client_name):
        missing.append("media_player")
    if entry.runtime_ms is None or entry.runtime_ms <= 0:
        missing.append("duration_ms")
    if row.play_duration is None or row.play_duration <= 0:
        missing.append("duration_played")

    return missing


def build_payload(entry: TrackEntry, row: PlaybackRow, listened_at: int) -> dict:
    return {
        "listened_at": listened_at,
        "track_metadata": {
            "artist_name": ", ".join(entry.artists),
            "release_name": entry.album,
            "track_name": entry.name,
            "additional_info": {
                "artist_mbids": list(entry.artist_mbids),
                "release_group_mbid": entry.provider_ids["MusicBrainzReleaseGroup"],
                "release_mbid": entry.provider_ids["MusicBrainzAlbum"],
                "track_mbid": entry.provider_ids["MusicBrainzTrack"],
                "tracknumber": entry.index_number,
                "discnumber": entry.parent_index_number,
                "media_player": row.client_name,
                "submission_client": MEDIA_BROWSER_CLIENT,
                "submission_client_version": VERSION,
                "duration_ms": entry.runtime_ms,
                "duration_played": row.play_duration,
            },
        },
    }


class MissingDatabaseError(Exception):
    """Raised when the Playback Reporting database is missing or unreadable."""


def iter_audio_rows(db_path: Path) -> Iterator[PlaybackRow]:
    """Yield Audio playback rows in the database's natural (oldest first) order."""
    if not db_path.exists():
        raise MissingDatabaseError(f"PLAYBACK_REPORTING_DB not found: {db_path}")

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        raise MissingDatabaseError(f"Cannot open {db_path}: {error}") from error

    try:
        cursor = connection.execute(
            "SELECT DateCreated, ItemName, ClientName, PlayDuration "
            "FROM PlaybackActivity "
            "WHERE ItemType = 'Audio' COLLATE NOCASE"
        )
        for date_created, item_name, client_name, play_duration in cursor:
            yield PlaybackRow(
                date_created=str(date_created or ""),
                item_name=str(item_name or ""),
                client_name=None if client_name is None else str(client_name),
                play_duration=None if play_duration is None else int(play_duration),
            )
    except sqlite3.Error as error:
        raise MissingDatabaseError(
            f"Cannot read PlaybackActivity from {db_path}: {error}. "
            "Make sure it is a Jellyfin Playback Reporting database; "
            "if Jellyfin is running, copy the database and use the copy."
        ) from error
    finally:
        connection.close()


JELLYFIN_PAGE_SIZE = 500
HTTP_TIMEOUT = (10, 60)
HTTP_RETRIES = 3
MAX_RETRY_DELAY = 60
RETRY_BACKOFF = (1, 2, 4)


def backoff_delay(attempt: int) -> float:
    return RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]


def retry_after_delay(response: Any) -> Optional[float]:
    """Parse a numeric ``Retry-After`` header; absent, malformed, or negative values return None."""
    value = (getattr(response, "headers", None) or {}).get("Retry-After")
    if value is None:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return min(delay, MAX_RETRY_DELAY)


class JellyfinError(Exception):
    """Raised for Jellyfin authentication, transport, or API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class JellyfinClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        device: str,
        device_id: str,
        session: Optional[requests.Session] = None,
        retries: int = HTTP_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        timeout: tuple[float, float] = HTTP_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.device = device
        self.device_id = device_id
        self._session = session if session is not None else requests.Session()
        self._retries = retries
        self._sleep = sleep
        self._timeout = timeout
        self._token: Optional[str] = None

    def _auth_header(self, token: Optional[str] = None) -> str:
        header = (
            f'MediaBrowser Client="{MEDIA_BROWSER_CLIENT}", Device="{self.device}", '
            f'DeviceId="{self.device_id}", Version="{VERSION}"'
        )
        if token:
            header += f', Token="{token}"'
        return header

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict] = None,
        with_token: bool = True,
    ) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        headers["Authorization"] = self._auth_header(self._token if with_token else None)

        attempt = 0
        while True:
            try:
                if method == "POST":
                    response = self._session.post(
                        url, headers=headers, json=json, timeout=self._timeout
                    )
                else:
                    response = self._session.get(
                        url, headers=headers, params=params, timeout=self._timeout
                    )
            except requests.RequestException as error:
                if attempt < self._retries:
                    self._sleep(backoff_delay(attempt))
                    attempt += 1
                    continue
                raise JellyfinError(f"Connection error talking to {url}: {error}") from error

            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError:
                    return {}

            if status in (401, 403):
                if path == "/Users/AuthenticateByName":
                    message = "Authentication failed. Check USERNAME and PASSWORD."
                else:
                    message = f"Jellyfin token was rejected (HTTP {status})."
                raise JellyfinError(message, status_code=status)

            if status == 429 or 500 <= status <= 599:
                if attempt < self._retries:
                    delay = retry_after_delay(response) or backoff_delay(attempt)
                    self._sleep(delay)
                    attempt += 1
                    continue

            snippet = (response.text or "")[:300]
            raise JellyfinError(
                f"Jellyfin request failed with HTTP {status}: {snippet}", status_code=status
            )

    def login(self) -> tuple[str, str]:
        data = self._request(
            "POST",
            "/Users/AuthenticateByName",
            json={"Username": self.username, "Pw": self.password},
            with_token=False,
        )
        token = data.get("AccessToken")
        user_id = (data.get("User") or {}).get("Id")
        if not is_present(token) or not is_present(user_id):
            raise JellyfinError("Login response missing AccessToken or user Id")
        self._token = str(token)
        return str(token), str(user_id)

    def _paginate(self, path: str, params: Mapping[str, Any]) -> list[dict]:
        collected: list[dict] = []
        start_index = 0
        pages = 0
        while True:
            page_params = dict(params)
            page_params["startIndex"] = start_index
            page_params["limit"] = JELLYFIN_PAGE_SIZE
            data = self._request("GET", path, params=page_params)
            items = data.get("Items") or []
            collected.extend(items)
            pages += 1
            if not items:
                return collected
            total = data.get("TotalRecordCount")
            if total is not None and len(collected) >= int(total):
                return collected
            start_index += len(items)
            if pages > 2000 or start_index > 1_000_000:
                raise JellyfinError(f"Pagination for {path} did not terminate")

    def fetch_all_artists(self, user_id: str) -> list[dict]:
        return self._paginate("/Artists", {"fields": "ProviderIds", "userId": user_id})

    def fetch_all_audio_items(self, user_id: str) -> list[dict]:
        return self._paginate(
            "/Items",
            {
                "userId": user_id,
                "recursive": "true",
                "fields": "ProviderIds",
                "includeItemTypes": "Audio",
            },
        )


BATCH_SIZE = 1000


class ListenBrainzError(Exception):
    """Raised when a ListenBrainz batch submission fails."""


def chunked(items: Any, size: int) -> Iterator[list]:
    chunk: list = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def build_submit_body(listens: list[dict]) -> dict:
    return {"listen_type": "import", "payload": listens}


class ListenBrainzClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        session: Optional[requests.Session] = None,
        retries: int = HTTP_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        timeout: tuple[float, float] = HTTP_TIMEOUT,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._session = session if session is not None else requests.Session()
        self._retries = retries
        self._sleep = sleep
        self._timeout = timeout

    def submit_batch(self, listens: list[dict]) -> None:
        url = f"{self.api_url}/1/submit-listens"
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Token {self.token}",
        }
        body = build_submit_body(listens)

        attempt = 0
        while True:
            try:
                response = self._session.post(url, headers=headers, json=body, timeout=self._timeout)
            except requests.RequestException as error:
                if attempt < self._retries:
                    self._sleep(backoff_delay(attempt))
                    attempt += 1
                    continue
                raise ListenBrainzError(f"Connection error talking to {url}: {error}") from error

            status = response.status_code
            if status == 200:
                data: Optional[dict] = None
                try:
                    parsed = response.json()
                    data = parsed if isinstance(parsed, dict) else None
                except ValueError:
                    data = None
                if data is not None and "status" in data and data["status"] != "ok":
                    raise ListenBrainzError(
                        f"ListenBrainz returned status {data['status']!r}: {response.text[:300]}"
                    )
                return

            if status == 400:
                raise ListenBrainzError(
                    f"ListenBrainz rejected the payload (HTTP 400): {(response.text or '')[:300]}"
                )
            if status == 401:
                raise ListenBrainzError("ListenBrainz rejected the token (HTTP 401). Check LISTENBRAINZ_USER_TOKEN.")

            if status == 429 or 500 <= status <= 599:
                if attempt < self._retries:
                    delay = retry_after_delay(response) or backoff_delay(attempt)
                    self._sleep(delay)
                    attempt += 1
                    continue

            raise ListenBrainzError(
                f"ListenBrainz request failed with HTTP {status}: {(response.text or '')[:300]}"
            )


DEVICE_ID_FILE = Path(".device_id")


class DeviceIdError(Exception):
    """Raised when the persistent device id cannot be read or written."""


def load_or_create_device_id(path: Path = DEVICE_ID_FILE) -> str:
    if path.exists():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise DeviceIdError(f"Cannot read device_id file {path}: {error}") from error
        if value:
            return value

    value = str(uuid.uuid4())
    try:
        path.write_text(value, encoding="utf-8")
    except OSError as error:
        raise DeviceIdError(f"Cannot write device_id file {path}: {error}") from error
    return value


@dataclass
class Stats:
    db_audio_rows: int = 0
    out_of_range: int = 0
    invalid_date: int = 0
    filtered_out: int = 0
    duplicate_key_plays: int = 0
    unmatched_plays: int = 0
    incomplete_plays: int = 0
    ready_plays: int = 0
    submitted_plays: int = 0
    batches_submitted: int = 0
    missing_by_field: dict[str, int] = field(default_factory=dict)
    jellyfin_items: int = 0
    indexed_tracks: int = 0
    unindexed_items: int = 0
    duplicate_keys: int = 0


def run(
    config: Config,
    submit: bool,
    jellyfin_client_factory: Callable[[str], JellyfinClient],
    listenbrainz_client: Optional[ListenBrainzClient] = None,
    device_id: str = "",
    out: Callable[[str], None] = print,
) -> Stats:
    stats = Stats()

    if not config.db_path.exists():
        raise MissingDatabaseError(f"PLAYBACK_REPORTING_DB not found: {config.db_path}")

    client = jellyfin_client_factory(device_id)
    _, user_id = client.login()
    out(f"Logged in to Jellyfin as {config.username}")

    artists = client.fetch_all_artists(user_id)
    artist_index = build_artist_index(artists)
    artists_without_mbid = sum(
        1
        for artist in artists
        if not is_present(
            artist_index.get(str(artist.get("Id")), {}).get("ProviderIds", {}).get("MusicBrainzArtist")
        )
    )
    if artists_without_mbid:
        out(f"Warning: {artists_without_mbid} of {len(artists)} artists have no MusicBrainzArtist id")

    items = client.fetch_all_audio_items(user_id)
    index = build_track_index(items, artist_index)
    stats.jellyfin_items = len(items)
    stats.indexed_tracks = len(index.tracks)
    stats.unindexed_items = index.unindexed
    stats.duplicate_keys = len(index.duplicate_keys)
    out(
        f"Jellyfin: {stats.jellyfin_items} audio items, {stats.indexed_tracks} tracks indexed, "
        f"{stats.unindexed_items} items without a usable key"
    )
    for key, jellyfin_ids in index.duplicate_keys.items():
        out(f"Warning: duplicate key skipped: {key} ({len(jellyfin_ids)} tracks)")
    if not index.tracks:
        raise JellyfinError("No Jellyfin audio tracks could be indexed")

    def iter_payloads() -> Iterator[dict]:
        for playback in iter_audio_rows(config.db_path):
            stats.db_audio_rows += 1
            try:
                played_at = parse_db_datetime(playback.date_created)
            except ValueError:
                stats.invalid_date += 1
                out(f"SKIP invalid DateCreated: {playback.date_created!r} [{playback.item_name}]")
                continue

            if not in_date_range(played_at.date(), config.start_date, config.end_date):
                stats.out_of_range += 1
                continue

            entry = index.tracks.get(playback.item_name)
            if entry is None:
                if playback.item_name in index.duplicate_keys:
                    stats.duplicate_key_plays += 1
                    out(f"SKIP duplicate key: {playback.item_name} [{playback.date_created}]")
                else:
                    stats.unmatched_plays += 1
                    out(f"SKIP unmatched: {playback.item_name} [{playback.date_created}]")
                continue

            if not passes_duration_filter(playback.play_duration, entry.runtime_ms):
                stats.filtered_out += 1
                continue

            missing = missing_fields(entry, playback)
            if missing:
                stats.incomplete_plays += 1
                for name in missing:
                    stats.missing_by_field[name] = stats.missing_by_field.get(name, 0) + 1
                out(
                    f"SKIP missing attributes: {playback.item_name} "
                    f"[{playback.date_created}] -> {', '.join(missing)}"
                )
                continue

            stats.ready_plays += 1
            yield build_payload(
                entry, playback, to_unix_timestamp(played_at, config.timezone)
            )

    for batch in chunked(iter_payloads(), BATCH_SIZE):
        if not submit:
            continue
        assert listenbrainz_client is not None
        number = stats.batches_submitted + 1
        listenbrainz_client.submit_batch(batch)
        stats.submitted_plays += len(batch)
        stats.batches_submitted += 1
        out(
            f"Submitted batch {number} ({len(batch)} listens, "
            f"{batch[0]['listened_at']} -> {batch[-1]['listened_at']})"
        )

    print_stats(out, stats, submit)
    return stats


def print_stats(out: Callable[[str], None], stats: Stats, submit: bool) -> None:
    mode = "submit" if submit else "check (dry run, nothing was submitted)"
    out("")
    out(f"Mode: {mode}")
    out(f"Playback DB: {stats.db_audio_rows} Audio rows")
    if stats.db_audio_rows == 0:
        out("No Audio playback records found in the database.")
        return
    out(f"  outside date range: {stats.out_of_range}")
    out(f"  invalid DateCreated: {stats.invalid_date}")
    out(f"  filtered out by play duration: {stats.filtered_out}")
    out(f"  ready to submit: {stats.ready_plays}")
    out(f"  unmatched track: {stats.unmatched_plays}")
    out(f"  duplicate key (skipped): {stats.duplicate_key_plays}")
    out(f"  missing attributes: {stats.incomplete_plays}")
    if stats.missing_by_field:
        summary = ", ".join(
            f"{name}={count}" for name, count in sorted(stats.missing_by_field.items())
        )
        out(f"Missing attribute summary: {summary}")
    if submit:
        out(f"Submitted: {stats.submitted_plays} listens in {stats.batches_submitted} batches")
        if stats.submitted_plays == 0:
            out("Nothing to submit.")
    else:
        out(f"Result: {stats.ready_plays} listens ready (check mode)")


def parse_args(argv: Optional[list[str]] = None) -> Namespace:
    parser = ArgumentParser(
        prog="scrobble-importer-jellyfin",
        description="Import Jellyfin Playback Reporting history into ListenBrainz.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Check matching and payload completeness without submitting (default).",
    )
    group.add_argument(
        "--submit",
        action="store_true",
        help="Build payloads and submit them to ListenBrainz.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)
    args.check = not args.submit
    return args


def main(
    argv: Optional[list[str]] = None,
    dotenv_path: Path = DEFAULT_DOTENV_PATH,
) -> int:
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10 or newer is required.", file=sys.stderr)
        return 2

    args = parse_args(argv)
    load_dotenv_default(dotenv_path)

    try:
        config = load_config(os.environ, submit=args.submit)
    except ConfigError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if not config.db_path.exists():
        print(f"ERROR: PLAYBACK_REPORTING_DB not found: {config.db_path}", file=sys.stderr)
        return 1

    try:
        device_id = load_or_create_device_id()
        jellyfin_client = JellyfinClient(
            base_url=config.jellyfin_url,
            username=config.username,
            password=config.password,
            device=config.device,
            device_id=device_id,
        )
        listenbrainz_client = None
        if args.submit:
            assert config.listenbrainz_token is not None
            listenbrainz_client = ListenBrainzClient(
                api_url=config.listenbrainz_api_url,
                token=config.listenbrainz_token,
            )
        run(
            config,
            submit=args.submit,
            jellyfin_client_factory=lambda _device_id: jellyfin_client,
            listenbrainz_client=listenbrainz_client,
            device_id=device_id,
        )
    except (MissingDatabaseError, JellyfinError, ListenBrainzError, DeviceIdError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
