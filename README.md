# Scrobble Importer Jellyfin

One-time import of historical listens from the Jellyfin Playback Reporting database into ListenBrainz.

The tool logs in to Jellyfin, builds an index of your audio tracks and their MusicBrainz IDs, reads every `Audio` row from the Playback Reporting SQLite database, matches each playback to a Jellyfin track, and submits the resulting listens to ListenBrainz with `listen_type: import`.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A Jellyfin server with the [Playback Reporting](https://github.com/jellyfin/jellyfin-plugin-playbackreporting) plugin
- A ListenBrainz user token (only needed for `--submit`)

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | Description |
| --- | --- | --- |
| `JELLYFIN_URL` | yes | Jellyfin base URL, for example `http://localhost:8096` |
| `USERNAME` | yes | Jellyfin user name |
| `PASSWORD` | yes (may be empty) | Jellyfin password; empty values are allowed for passwordless accounts |
| `PLAYBACK_REPORTING_DB` | yes | Path to `playback_reporting.db` |
| `LISTENBRAINZ_USER_TOKEN` | `--submit` only | ListenBrainz user token |
| `LISTENBRAINZ_API_URL` | no | Defaults to `https://api.listenbrainz.org` |
| `DEVICE` | no | Jellyfin device name, defaults to `Python` |
| `TZ` | no | Timezone used to interpret `DateCreated`, defaults to `UTC` |
| `START_DATE` | no | Inclusive start date, `YYYY-MM-DD` |
| `END_DATE` | no | Inclusive end date, `YYYY-MM-DD` |

Variables already present in the environment take precedence over `.env`. A `.device_id` file is created in the working directory and reused across runs.

On Windows, the generic `USERNAME` variable is also set by the operating system, so it wins over `.env`. Set your Jellyfin user name in the shell that runs the tool (for example `set USERNAME=jonathan`) or unset it before running.

## Usage

Check first (the default; nothing is submitted). Use the `uv run` commands below so the correct Python environment is used:

```bash
uv run scrobble-importer-jellyfin --check
# or
uv run python scrobble_importer_jellyfin.py --check
```

Submit once the check output looks right:

```bash
uv run scrobble-importer-jellyfin --submit
```

`--check` logs in to Jellyfin, builds the index, reads the database, builds every payload, and reports:

- how many Jellyfin tracks were indexed, and duplicate keys that were skipped
- each unmatched playback and each playback missing required attributes
- counts for ready, unmatched, duplicate-key, and missing-attribute playbacks

`--submit` performs the same work and uploads listens in batches of 1000.

Exit codes: `0` success, `1` runtime/network error, `2` configuration or CLI error.

## Matching and submission rules

- The track index key is `{AlbumArtist} - {Name} ({Album})`, using the Jellyfin item fields verbatim. Matching against `PlaybackActivity.ItemName` is exact; no case or whitespace normalization is applied.
- Artist MusicBrainz IDs come from the track's `ArtistItems` (in order) looked up in the `/Artists` index. Album artists are used only for the matching key.
- A playback is kept when `PlayDuration >= 240` seconds or `PlayDuration * 1000 >= RunTimeMs / 2`.
- Every payload field is required. Playbacks missing any of `artist_name`, `release_name`, `track_name`, `artist_mbids` (for every artist), `release_group_mbid`, `release_mbid`, `track_mbid`, `tracknumber`, `discnumber`, `media_player`, `duration_ms`, or `duration_played` are reported and skipped.
- If two Jellyfin tracks produce the same index key, the key is reported and **all** tracks for that key are skipped.
- `TZ` is used only to convert `DateCreated` to a Unix timestamp. `START_DATE`/`END_DATE` compare the date portion of `DateCreated` directly, without timezone conversion.
- Playback rows are not filtered by `UserId`; every `Audio` row in the database is processed.
- `TZ` should match the timezone in which Jellyfin wrote `DateCreated`, otherwise `listened_at` timestamps will be shifted.

## Notes

- This is a one-time, stateless import. Running `--submit` twice submits the same listens again. To resume after a failed run, set `START_DATE` to the day after the last successfully submitted date.
- If Jellyfin is running and the database is locked, copy `playback_reporting.db` and point `PLAYBACK_REPORTING_DB` at the copy.
- Duplicate-key and missing-attribute reports are printed for every affected playback row; use `START_DATE`/`END_DATE` to narrow large runs.

## Development

```bash
uv run pytest
```

The test suite is fully offline; it uses fixture JSON from `samples/` and temporary SQLite databases.

## License

GPL-3.0-only. See `LICENSE`.
