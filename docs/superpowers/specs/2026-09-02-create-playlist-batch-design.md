# Batch upsert of station playlists into Spotify

Date: 2026-09-02
Status: approved, ready for an implementation plan
Source: `docs/propose_20260902.md`

## Problem

Today the only path from a stored CSV to Spotify is `/create_playlist_from_file`, which
creates a **new** playlist named after the CSV. A station that is scraped nightly
therefore accumulates one throwaway Spotify playlist per day, and there is no way to
build "everything Radio FM played between two dates" into a single durable playlist.

Three things are missing:

1. A declaration of which Spotify playlist a given radio station belongs to.
2. An API that upserts a date range of a station's CSVs into that playlist.
3. Automation, so newly scraped files reach Spotify without anyone clicking.

## Scope

In scope: the station-to-playlist config, `POST /create-playlist-batch`, the nightly
batch over newly uploaded files, server-side token persistence to make that nightly
batch possible, and a form on the playlists page to drive the endpoint.

Out of scope: changing the scrapers, changing how track matching works (still one
Spotify search per unique artist/song), and any change to `/create_playlist_from_file`
or `/merge_playlists`.

## Decisions

Every decision below was made explicitly during brainstorming; none is a default.

| Question | Decision |
|---|---|
| Config value | Playlist **name**; resolve by exact name, create if missing |
| Config location | `station_playlists.json` at the repo root |
| Date range meaning | The **load date in the filename**, not the play time inside the CSV |
| Range bounds | Both endpoints inclusive |
| Duplicates | Dedupe within the batch **and** against the target playlist's existing URIs |
| Unmatched tracks | Returned in full, as a list, in the task result |
| Nightly auth | A refresh token persisted server-side |
| Token location | `DATA_DIR/spotify_token.json`, mode `0600` |
| Token capture | Automatically on every OAuth callback |
| New-file tracking | `processed_playlists.json`, an object in the S3 bucket |
| Nightly trigger | Inside the existing 23:40 job, immediately after the scrape |
| Endpoint shape | Async `task_id` + `/playlist_progress` polling |
| Station ids | Opaque strings, so `retrofm` works alongside numeric Radoxo ids |
| UI | A form on the playlists page; no separate manual catch-up endpoint |

## Security note

A stored refresh token is a long-lived credential granting playlist read/write on the
user's Spotify account until it is revoked. It is deliberately kept out of S3 (which
holds the comparatively public CSVs) and off git (`data` is already in `.gitignore`),
written owner-only, and cleared on logout. This risk was stated and accepted in order
to make the nightly pipeline unattended.

## Architecture

Three new modules, rather than growing `spotify_playlist.py` (already ~700 lines):

- **`station_playlists.py`** — loads and validates the JSON config.
- **`spotify_token_store.py`** — persists, loads and clears the refresh token.
- **`playlist_batch.py`** — filename parsing, S3 file selection, the processed marker,
  and batch orchestration.

Existing modules change as follows:

- `spotify_playlist.py` gains `upsert_tracks_into_playlist` and a token-store write in
  `handle_oauth_callback` / `clear_spotify_token`.
- `playlist_upload.py` gains `put_object_to_s3(bucket, key, content) -> bool`.
- `app.py` gains two routes and one call inside `my_scheduled_job`.
- `static/ts/` gains a batch form component.

Data flow, unchanged up to S3:

```
scrape -> CSV on disk -> S3 (radio-playlists)
                          |
                          +-- POST /create-playlist-batch (session token)  --+
                          |                                                  +-> upsert into configured Spotify playlist
                          +-- nightly batch, new files only (stored token) --+
```

### 1. `station_playlists.py`

```json
{ "16134": "Radio FM", "38225": "Hit FM", "retrofm": "Retro FM" }
```

- Keys are opaque strings. Nothing may assume a numeric station id; `retrofm` is a
  slug and is scraped by a different source into the same bucket with the same
  filename shape.
- Path defaults to `station_playlists.json` beside the module, overridable with the
  `STATION_PLAYLISTS_CONFIG` environment variable so a container can mount its own.
- A missing file, malformed JSON, a non-object top level, or a non-string/empty value
  logs an error and yields an **empty mapping**. The app still boots; the endpoint
  answers `400 station not configured`. Individual invalid entries are skipped with a
  warning rather than discarding the whole file.
- Public surface: `get_playlist_name(station_id) -> str | None` and
  `get_configured_stations() -> dict[str, str]`.

Playlist resolution happens in `spotify_playlist.py`, not here: list the user's
playlists, match on exact name, create a private playlist if none matches. If several
playlists share the name, the first is used and a warning is logged.

### 2. `spotify_token_store.py`

- Location: `os.path.join(load_playlist.DATA_DIR, "spotify_token.json")`, overridable
  with `SPOTIFY_TOKEN_STORE`.
- `save_token(token_info) -> bool`, `load_token() -> dict | None`, `clear_token() -> bool`.
- Written to a temporary file in the same directory opened with
  `os.open(..., O_CREAT | O_WRONLY | O_TRUNC, 0o600)`, then `os.replace`d into place.
  Opening with the mode rather than chmod-ing afterwards means the file is never even
  briefly world-readable, and the rename makes a torn write impossible.
- Every function swallows its exceptions and signals through the return value,
  matching the convention `playlist_upload.py` already sets.
- `handle_oauth_callback` writes the store **after** the session write. A store failure
  is logged and ignored: failing to arm the nightly job must not break interactive login.
- `clear_spotify_token` (used by `/spotify/logout`) also clears the store. Leaving an
  unattended credential armed after an explicit logout is the wrong surprise.

**Refresh-token rotation.** `create_spotify_client_from_store()` builds the client over
a write-through mapping — a small `dict` subclass whose `__setitem__` re-persists the
file — passed to the existing `SessionCacheHandler`. Without this, a token spotipy
refreshes during a nightly run would be written into a throwaway dict and lost, which
is exactly the failure mode CLAUDE.md documents for thread-copied sessions. The
function returns `None` when the store is empty, so spotipy can never fall back to its
interactive console flow inside the scheduler thread.

### 3. `playlist_batch.py`

**Filename parsing.** `parse_playlist_key(key)` matches
`^playlist_(?P<station>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.csv$` and returns
`(station_id, date)` or `None`. The station group is greedy and anchored by the fixed
trailing date/time/extension, so a station id containing an underscore still parses.
Keys that do not match are ignored, not an error: the bucket also holds
`processed_playlists.json`.

**Selection.** `select_keys(keys, station_id, start_date, end_date)` returns the
matching keys sorted, comparing the filename's `YYYYMMDD` against both bounds
inclusively.

**Track collection.** `collect_tracks(bucket, keys)` downloads each key, parses it with
pandas, and returns unique `(artist_name, song_name)` pairs in first-seen order.
Empty or unparseable CSVs are skipped with a warning — the bucket still contains
one-byte files from the pre-`NoTracksFoundError` era.

**Processed marker.** `processed_playlists.json` in the `radio-playlists` bucket:

```json
{ "processed": ["playlist_16134_20260814_072254.csv"], "updated_at": "2026-09-02T23:40:00" }
```

Read once at the start of a nightly run, written once at the end. **The endpoint never
touches it**: a manual range batch is already idempotent through dedupe, and letting it
write the marker would silently exclude those files from a later nightly run. A key is recorded
**only when the upsert that consumed it succeeded**, so a Spotify outage leaves those
files to be retried the next night instead of silently swallowing them. The read and
write are guarded by a module-level lock; uWSGI runs a single process, so that lock is
sufficient.

**Orchestration.**

- `run_range_batch(station_id, start_date, end_date, task_id, session_data)` — backs
  the endpoint.
- `process_new_playlists()` — backs the cron job. Loads the stored token (absent → log
  a warning and return, never raise inside the scheduler), lists the bucket, keeps
  configured stations, subtracts already-processed keys, groups by station, upserts
  each, and records the successful keys.

### 4. `upsert_tracks_into_playlist` (in `spotify_playlist.py`)

Given a client, a playlist name and unique `(artist, song)` pairs:

1. Resolve or create the playlist by name.
2. Read the playlist's existing track URIs into a set. The paging loop currently lives
   inside `get_playlist_tracks_with_session`, which takes `session_data` and builds its
   own client; it is extracted into a client-based `get_playlist_tracks(sp, playlist_id)`
   that both callers use, so the upsert never rebuilds a client it was already handed.
3. One `sp.search` per unique pair, reusing `search_track`.
4. Keep only URIs not already in the playlist, deduped against each other.
5. Add in batches of 100 (the Spotify API limit).
6. Report progress through `update_task` throughout.

Returns `{"playlist_id", "playlist_name", "added", "skipped_existing", "unmatched": [{"artist", "song"}]}`.

### 5. Routes

**`POST /create-playlist-batch`**, body `{"station_id", "start_date", "end_date"}`:

| Condition | Response |
|---|---|
| Missing/invalid JSON body or fields | `400` |
| `station_id` not in the config | `400`, naming the station |
| Dates not `YYYY-MM-DD` | `400` |
| `start_date > end_date` | `400` |
| No Spotify token in the session | `401 SPOTIFY_AUTH_REQUIRED` |
| Otherwise | `200 {"status": "success", "task_id": ...}` |

The session is copied with `dict(session)` **in the request context** before the thread
starts, per the rule in CLAUDE.md.

**`GET /api/station-playlists`** returns `{"status": "success", "stations": {...}}` so the
UI can populate its dropdown.

**`/playlist_progress/<task_id>`** gains a nullable `result` key carrying the upsert
result described above. Existing callers ignore unknown keys, so nothing breaks.

### 6. Cron

`my_scheduled_job` calls `playlist_batch.process_new_playlists()` after
`scrape_and_upload_playlists()`, inside its own try/except so a Spotify failure cannot
prevent the scrape's result from being logged. Both outcomes appear in one log line for
the run.

### 7. Frontend

A `BatchPlaylistForm` component on `PlaylistsPage`: a station `<select>` populated from
`/api/station-playlists`, two `<input type="date">` fields, and a submit button that
POSTs and then polls `/playlist_progress/<id>` with the existing `ProgressBar`. On
completion it shows `added` / `skipped_existing` counts and the unmatched list.
`PlaylistProgress` in `types.ts` gains the optional `result` field. Styling follows
`components/styles.ts`. Requires `cd static && npm run build`.

## Error handling

- Config problems degrade to `400`, never `500`.
- A station whose upsert fails does not abort the other stations in a nightly run;
  failures are collected and logged, mirroring `scrape_and_upload_playlists`.
- S3 helpers keep signalling failure by return value; every new call site checks it.
- The scheduler thread never propagates an exception.

## Testing

Python, pytest, written test-first:

- `parse_playlist_key` on well-formed keys, the marker file, underscored station ids,
  and junk.
- `select_keys` inclusivity at both bounds and station isolation.
- Config loading: valid file, missing file, malformed JSON, invalid entries.
- Token store: round-trip, `0600` permissions, atomic replace, write-through refresh,
  clear.
- `upsert_tracks_into_playlist` against a fake `sp`: dedupe within batch, dedupe
  against existing playlist URIs, playlist created when absent, unmatched tracks
  reported, batching at 100.
- `process_new_playlists`: only successful keys are marked processed; a missing token
  is a no-op.
- Route validation: each `400` case and the `401`.

Network paths to Spotify and S3 stay untested, matching the existing suite.

Frontend: type-check only (`npx tsc --noEmit`); the repo has no frontend test runner.

## Documentation

`CLAUDE.md` gains a short section covering the config file, the token store and its
security posture, and the processed marker. `.env.template` gains
`STATION_PLAYLISTS_CONFIG` and `SPOTIFY_TOKEN_STORE` as optional overrides.
