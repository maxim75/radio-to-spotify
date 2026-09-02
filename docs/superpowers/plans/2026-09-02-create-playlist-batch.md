# Batch Playlist Upsert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upsert a date range of a radio station's stored CSVs into one configured Spotify playlist, on demand from the UI and automatically each night for newly scraped files.

**Architecture:** Three new modules — `station_playlists.py` (config), `spotify_token_store.py` (server-side refresh token), `playlist_batch.py` (file selection and orchestration) — plus one new function in `spotify_playlist.py`, one in `playlist_upload.py`, two routes in `app.py`, and a form component in `static/ts/`. The existing in-process task store and `/playlist_progress` polling carry progress; nothing about scraping changes.

**Tech Stack:** Python 3.13, Flask, pandas, boto3, spotipy, APScheduler, pytest; React 18 + TypeScript + styled-components, built by Vite.

**Spec:** `docs/superpowers/specs/2026-09-02-create-playlist-batch-design.md`

## Global Constraints

- Python deps are managed by **uv**. Run tests with `uv run pytest`. Never `pip install`.
- All Python modules are flat at the repo root; tests import them directly (pytest `pythonpath = ["."]`).
- The S3 bucket is the literal string `"radio-playlists"`, as elsewhere in the repo.
- `playlist_upload.py` functions **swallow exceptions and signal failure by return value**. Every new call site must check the return value. New functions in that module follow the same contract.
- Request-scoped code passes the live Flask `session`. Background threads get a `dict(session)` copy captured **in the request context before `thread.start()`**.
- Never build a spotipy client without a token first — `create_spotify_client_with_session` returns `None` by design, because spotipy otherwise falls back to an interactive console prompt and raises `EOFError` in a uWSGI worker.
- Never reach into `spotify_playlist._tasks`; use `start_task` / `update_task` / `get_task`.
- Track CSVs are Cyrillic. Always pass `encoding="utf-8"` explicitly when opening files.
- Tests must not make network calls to Spotify or S3. Use `monkeypatch` and fakes.
- Frontend: after any `.tsx` change run `cd static && npm run build`. There is no dev server.
- Every task ends with a commit.

---

### Task 1: Station-to-playlist configuration

**Files:**
- Create: `station_playlists.py`
- Create: `station_playlists.json`
- Test: `tests/test_station_playlists.py`

**Interfaces:**
- Consumes: nothing
- Produces: `station_playlists.load_config(path=None) -> dict[str, str]`, `station_playlists.get_configured_stations() -> dict[str, str]`, `station_playlists.get_playlist_name(station_id) -> str | None`. Keys are **strings**, never ints.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_station_playlists.py`:

```python
"""
Loading the station-to-playlist map.

A broken config file must never take the app down: the endpoint answers 400
"not configured" rather than 500, so every failure path here returns a mapping
rather than raising.
"""

import json

import station_playlists


def write_config(tmp_path, payload):
    path = tmp_path / "station_playlists.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_a_valid_config_is_loaded(tmp_path):
    path = write_config(tmp_path, {"16134": "Radio FM", "retrofm": "Retro FM"})

    assert station_playlists.load_config(path) == {
        "16134": "Radio FM",
        "retrofm": "Retro FM",
    }


def test_station_ids_are_strings_even_when_written_as_numbers(tmp_path):
    # json.dumps writes an int key as a string, but a hand-edited file or a future
    # caller may pass an int; nothing downstream may assume a numeric id, because
    # retrofm is a slug.
    path = write_config(tmp_path, {16134: "Radio FM"})

    assert station_playlists.load_config(path) == {"16134": "Radio FM"}


def test_a_missing_file_yields_an_empty_mapping(tmp_path):
    assert station_playlists.load_config(str(tmp_path / "nope.json")) == {}


def test_malformed_json_yields_an_empty_mapping(tmp_path):
    path = tmp_path / "station_playlists.json"
    path.write_text("{not json", encoding="utf-8")

    assert station_playlists.load_config(str(path)) == {}


def test_a_non_object_top_level_yields_an_empty_mapping(tmp_path):
    path = write_config(tmp_path, ["16134", "Radio FM"])

    assert station_playlists.load_config(path) == {}


def test_invalid_entries_are_skipped_without_discarding_the_file(tmp_path):
    path = write_config(tmp_path, {
        "16134": "Radio FM",
        "38225": "",
        "38234": None,
        "retrofm": "   Retro FM   ",
    })

    assert station_playlists.load_config(path) == {
        "16134": "Radio FM",
        "retrofm": "Retro FM",
    }


def test_get_playlist_name_returns_none_for_an_unconfigured_station(tmp_path, monkeypatch):
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", write_config(tmp_path, {"16134": "Radio FM"}))

    assert station_playlists.get_playlist_name("16134") == "Radio FM"
    assert station_playlists.get_playlist_name(16134) == "Radio FM"
    assert station_playlists.get_playlist_name("99999") is None


def test_get_configured_stations_reads_the_default_path(tmp_path, monkeypatch):
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", write_config(tmp_path, {"16134": "Radio FM"}))

    assert station_playlists.get_configured_stations() == {"16134": "Radio FM"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_station_playlists.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'station_playlists'`

- [ ] **Step 3: Write the implementation**

Create `station_playlists.py`:

```python
"""
Which Spotify playlist each radio station's tracks belong to.

Station ids are opaque strings, not numbers: Radoxo ids look like "16134" but the
radiotut source uses the slug "retrofm", and both write files into the same bucket
with the same filename shape.

Every failure here degrades to an empty (or partial) mapping rather than raising.
A bad config file must not stop the app from booting - the batch endpoint answers
400 "station not configured", which is a far more useful thing for a caller to read
than a 500 from an import-time crash.
"""

import json
import logging
import os

# Overridable so a container can mount its own file without rebuilding the image.
CONFIG_PATH = os.environ.get(
    "STATION_PLAYLISTS_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "station_playlists.json"),
)


def load_config(path=None):
    """Read the station -> playlist-name map. Returns {} if it cannot be read."""
    path = path or CONFIG_PATH

    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        logging.error(
            "Station playlist config not found at %s - no station is configured, so "
            "/create-playlist-batch will reject every request.", path
        )
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logging.error("Could not read station playlist config %s: %s", path, e)
        return {}

    if not isinstance(raw, dict):
        logging.error(
            "Station playlist config %s must be a JSON object mapping station id to "
            "playlist name, got %s", path, type(raw).__name__
        )
        return {}

    config = {}
    for station_id, playlist_name in raw.items():
        if not isinstance(playlist_name, str) or not playlist_name.strip():
            logging.warning(
                "Skipping station %r in %s: the playlist name must be a non-empty string",
                station_id, path
            )
            continue
        config[str(station_id)] = playlist_name.strip()

    return config


def get_configured_stations():
    """The full station -> playlist-name map from the default config path."""
    return load_config()


def get_playlist_name(station_id):
    """The configured playlist name for a station, or None if it is not configured."""
    return load_config().get(str(station_id))
```

Create `station_playlists.json` with the station ids this repo already scrapes. The
names are the user's to choose; these are starting values.

```json
{
  "retrofm": "Retro FM",
  "16134": "Radio 16134",
  "38225": "Radio 38225",
  "38234": "Radio 38234"
}
```

Note: `load_config` re-reads the file on every call rather than caching it at import.
The file is tiny, and this means editing it takes effect without restarting the app.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_station_playlists.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add station_playlists.py station_playlists.json tests/test_station_playlists.py
git commit -m "Add station-to-playlist configuration"
```

---

### Task 2: Server-side Spotify token store

**Files:**
- Create: `spotify_token_store.py`
- Test: `tests/test_spotify_token_store.py`

**Interfaces:**
- Consumes: `load_playlist.DATA_DIR`
- Produces: `spotify_token_store.save_token(token_info) -> bool`, `load_token() -> dict | None`, `clear_token() -> bool`, `token_path() -> str`, and the class `WriteThroughTokenStore(dict)` whose `__setitem__` re-persists the file when the key is `spotify_token_info`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spotify_token_store.py`:

```python
"""
Persistence of the Spotify refresh token used by the unattended nightly job.

This file is a long-lived credential granting playlist read/write on the user's
Spotify account. Two properties matter and are asserted here: it is never
world-readable, and a token spotipy refreshes mid-job is written back rather
than lost (the same class of bug as passing a dict(session) copy to a thread).
"""

import json
import os

import pytest

import spotify_token_store


TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at": 1234567890}


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "spotify_token.json")
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", path)
    return path


def test_a_saved_token_round_trips(store):
    assert spotify_token_store.save_token(TOKEN) is True

    assert spotify_token_store.load_token() == TOKEN


def test_the_token_file_is_owner_only(store):
    spotify_token_store.save_token(TOKEN)

    assert oct(os.stat(store).st_mode & 0o777) == "0o600"


def test_loading_an_absent_store_returns_none(store):
    assert spotify_token_store.load_token() is None


def test_a_token_without_a_refresh_token_is_rejected(store):
    # Without a refresh token the unattended job cannot outlive the access token's
    # hour, so treat it as no stored credential at all rather than a working one.
    with open(store, "w", encoding="utf-8") as handle:
        json.dump({"access_token": "a"}, handle)

    assert spotify_token_store.load_token() is None


def test_malformed_json_is_rejected(store):
    with open(store, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    assert spotify_token_store.load_token() is None


def test_saving_replaces_an_existing_token(store):
    spotify_token_store.save_token(TOKEN)
    spotify_token_store.save_token({**TOKEN, "access_token": "b"})

    assert spotify_token_store.load_token()["access_token"] == "b"


def test_saving_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "spotify_token.json"))

    spotify_token_store.save_token(TOKEN)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["spotify_token.json"]


def test_clear_token_removes_the_file(store):
    spotify_token_store.save_token(TOKEN)

    assert spotify_token_store.clear_token() is True
    assert not os.path.exists(store)


def test_clear_token_on_an_absent_store_reports_nothing_removed(store):
    assert spotify_token_store.clear_token() is False


def test_save_reports_failure_rather_than_raising(tmp_path, monkeypatch):
    # A store failure must never break interactive login, so it is signalled by the
    # return value like everything else in this codebase.
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "no" / "such" / "dir" / "t.json"))
    monkeypatch.setattr(spotify_token_store.os, "makedirs", lambda *a, **k: None)

    assert spotify_token_store.save_token(TOKEN) is False


def test_the_write_through_store_persists_a_refreshed_token(store):
    session_data = spotify_token_store.WriteThroughTokenStore({"spotify_token_info": TOKEN})

    refreshed = {**TOKEN, "access_token": "refreshed"}
    session_data["spotify_token_info"] = refreshed

    assert spotify_token_store.load_token() == refreshed


def test_the_write_through_store_ignores_unrelated_keys(store):
    session_data = spotify_token_store.WriteThroughTokenStore()

    session_data["something_else"] = "value"

    assert spotify_token_store.load_token() is None


def test_constructing_the_write_through_store_does_not_write(store):
    spotify_token_store.WriteThroughTokenStore({"spotify_token_info": TOKEN})

    assert not os.path.exists(store)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_spotify_token_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotify_token_store'`

- [ ] **Step 3: Write the implementation**

Create `spotify_token_store.py`:

```python
"""
Server-side persistence of the Spotify token, so the nightly job can reach Spotify
without a browser session.

This file holds a long-lived refresh token: anyone who reads it can read and modify
the user's playlists until the token is revoked. It therefore lives on the mounted
volume (already in .gitignore) rather than in the S3 bucket next to the comparatively
public CSVs, it is written owner-only, and /spotify/logout deletes it.

Like playlist_upload.py, every function swallows its exception and signals through the
return value: failing to persist the token must never break interactive login.
"""

import json
import logging
import os
import tempfile

import load_playlist


def token_path():
    """Where the token is stored. SPOTIFY_TOKEN_STORE overrides the volume default."""
    return os.environ.get("SPOTIFY_TOKEN_STORE") or os.path.join(
        load_playlist.DATA_DIR, "spotify_token.json"
    )


def save_token(token_info):
    """Write the token owner-only and atomically. Returns True when it landed."""
    path = token_path()
    directory = os.path.dirname(path) or "."
    handle = None
    tmp_path = None

    try:
        os.makedirs(directory, exist_ok=True)
        # mkstemp creates with mode 0600, so the file is never even briefly
        # world-readable; os.replace then makes a torn write impossible.
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".spotify_token.", suffix=".tmp")
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(token_info, handle)
        handle.close()
        handle = None
        os.replace(tmp_path, path)
        logging.info("Stored the Spotify token at %s", path)
        return True
    except Exception as e:
        logging.error("Could not store the Spotify token at %s: %s", path, e)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False


def load_token():
    """The stored token, or None when there is no usable one."""
    path = token_path()

    try:
        with open(path, encoding="utf-8") as handle:
            token_info = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.error("Could not read the Spotify token at %s: %s", path, e)
        return None

    # An access token with no refresh token expires within the hour and cannot be
    # renewed unattended, so it is no more use to the nightly job than nothing.
    if not isinstance(token_info, dict) or not token_info.get("refresh_token"):
        logging.error("The Spotify token at %s has no refresh_token - ignoring it", path)
        return None

    return token_info


def clear_token():
    """Delete the stored token. Returns True only if a file was actually removed."""
    path = token_path()

    try:
        os.unlink(path)
        logging.info("Cleared the stored Spotify token at %s", path)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logging.error("Could not clear the Spotify token at %s: %s", path, e)
        return False


class WriteThroughTokenStore(dict):
    """
    A session-shaped mapping that re-persists the token file when spotipy refreshes.

    The nightly job has no Flask session, so it hands SessionCacheHandler one of these
    instead. Without the write-through, a token refreshed mid-run would be written into
    a throwaway dict and lost - the same failure as passing dict(session) to a thread.

    dict.__init__ does not call __setitem__, so constructing one never writes; only a
    later assignment does.
    """

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "spotify_token_info":
            save_token(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_spotify_token_store.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add spotify_token_store.py tests/test_spotify_token_store.py
git commit -m "Add owner-only server-side store for the Spotify token"
```

---

### Task 3: Wire the token store into login, logout, and an unattended client

**Files:**
- Modify: `spotify_playlist.py` (add `import spotify_token_store` at the top; edit `handle_oauth_callback` and `clear_spotify_token`; add `create_spotify_client_from_store`)
- Test: `tests/test_spotify_token_wiring.py`

**Interfaces:**
- Consumes: `spotify_token_store.save_token`, `load_token`, `clear_token`, `WriteThroughTokenStore` (Task 2)
- Produces: `spotify_playlist.create_spotify_client_from_store() -> spotipy.Spotify | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spotify_token_wiring.py`:

```python
"""
The stored token is armed by logging in and disarmed by logging out.

Leaving an unattended credential live after an explicit logout is the wrong
surprise, and an unattended client built with no token is worse than none: spotipy
falls back to its interactive console flow and dies on EOF inside the scheduler.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import spotify_playlist
import spotify_token_store


TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at": 9999999999}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "spotify_token.json"))
    return str(tmp_path / "spotify_token.json")


class FakeAuthManager:
    def __init__(self, cache_handler):
        self.cache_handler = cache_handler

    def get_access_token(self, code, check_cache=False):
        return TOKEN


def test_a_successful_login_arms_the_store(store, monkeypatch):
    session = {}
    monkeypatch.setattr(
        spotify_playlist, "create_spotify_auth_manager",
        lambda session_data=None: FakeAuthManager(spotify_playlist.SessionCacheHandler(session_data)),
    )

    assert spotify_playlist.handle_oauth_callback("code", session) is True
    assert session["spotify_token_info"] == TOKEN
    assert spotify_token_store.load_token() == TOKEN


def test_a_store_failure_does_not_break_login(store, monkeypatch):
    session = {}
    monkeypatch.setattr(
        spotify_playlist, "create_spotify_auth_manager",
        lambda session_data=None: FakeAuthManager(spotify_playlist.SessionCacheHandler(session_data)),
    )
    monkeypatch.setattr(spotify_token_store, "save_token", lambda token_info: False)

    assert spotify_playlist.handle_oauth_callback("code", session) is True
    assert session["spotify_token_info"] == TOKEN


def test_logout_clears_the_store(store):
    spotify_token_store.save_token(TOKEN)
    session = {"spotify_token_info": TOKEN}

    assert spotify_playlist.clear_spotify_token(session) is True
    assert "spotify_token_info" not in session
    assert spotify_token_store.load_token() is None


def test_logout_clears_the_store_even_with_no_session_token(store):
    spotify_token_store.save_token(TOKEN)

    spotify_playlist.clear_spotify_token({})

    assert spotify_token_store.load_token() is None


def test_no_stored_token_means_no_unattended_client(store):
    assert spotify_playlist.create_spotify_client_from_store() is None


def test_an_unattended_client_is_built_over_a_write_through_store(store, monkeypatch):
    spotify_token_store.save_token(TOKEN)
    captured = {}

    def fake_create(session_data):
        captured["session_data"] = session_data
        return "spotify-client"

    monkeypatch.setattr(spotify_playlist, "create_spotify_client_with_session", fake_create)

    assert spotify_playlist.create_spotify_client_from_store() == "spotify-client"
    assert isinstance(captured["session_data"], spotify_token_store.WriteThroughTokenStore)
    assert captured["session_data"]["spotify_token_info"] == TOKEN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_spotify_token_wiring.py -v`
Expected: FAIL — `test_a_successful_login_arms_the_store` fails because nothing writes the store, and `create_spotify_client_from_store` raises `AttributeError`.

- [ ] **Step 3: Write the implementation**

In `spotify_playlist.py`, add to the imports near the top (after `from playlist_upload import ...`):

```python
import spotify_token_store
```

In `handle_oauth_callback`, replace this block:

```python
        # Save the token info for future use
        auth_manager.cache_handler.save_token_to_cache(token_info)
        logging.info("Successfully saved Spotify access token to the session")
        return True
```

with:

```python
        # Save the token info for future use
        auth_manager.cache_handler.save_token_to_cache(token_info)
        logging.info("Successfully saved Spotify access token to the session")

        # Also persist it server-side so the nightly job can reach Spotify with no
        # browser session. A failure here is logged and ignored: being unable to arm
        # the unattended job must not stop this user from logging in.
        if not spotify_token_store.save_token(token_info):
            logging.error(
                "Logged in, but the Spotify token could not be stored - the nightly "
                "batch will skip Spotify until this is fixed"
            )
        return True
```

In `clear_spotify_token`, replace the body after the `session_data is None` guard:

```python
        if 'spotify_token_info' in session_data:
            del session_data['spotify_token_info']
            logging.info("Spotify token cleared from session")
            return True
        return False
```

with:

```python
        # Clear the server-side store too. An explicit logout that leaves the
        # unattended credential armed is the wrong surprise.
        spotify_token_store.clear_token()

        if 'spotify_token_info' in session_data:
            del session_data['spotify_token_info']
            logging.info("Spotify token cleared from session")
            return True
        return False
```

Add `create_spotify_client_from_store` immediately after `create_spotify_client_with_session`:

```python
def create_spotify_client_from_store():
    """
    Build a Spotify client from the token persisted on disk, for unattended jobs.

    Returns None when nothing is stored - the scheduler thread has no console, so a
    client built without a token would print "Enter the URL you were redirected to:"
    and raise EOFError.

    The client is built over a WriteThroughTokenStore rather than a plain dict, so a
    token spotipy refreshes during the run is persisted instead of being discarded.
    """
    token_info = spotify_token_store.load_token()
    if not token_info:
        logging.warning(
            "No stored Spotify token - unattended jobs cannot reach Spotify. Connect "
            "a Spotify account once to arm them."
        )
        return None

    session_data = spotify_token_store.WriteThroughTokenStore(
        {'spotify_token_info': token_info}
    )
    return create_spotify_client_with_session(session_data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_spotify_token_wiring.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite to check nothing regressed**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add spotify_playlist.py tests/test_spotify_token_wiring.py
git commit -m "Arm and disarm the stored Spotify token with login and logout"
```

---

### Task 4: Playlist filename parsing and date-range selection

**Files:**
- Create: `playlist_batch.py`
- Test: `tests/test_playlist_batch_selection.py`

**Interfaces:**
- Consumes: nothing
- Produces: `playlist_batch.BUCKET_NAME` (`"radio-playlists"`), `parse_playlist_key(key) -> tuple[str, str] | None` returning `(station_id, "YYYYMMDD")`, `parse_iso_date(value) -> datetime.date` (raises `ValueError`), `select_keys(keys, station_id, start_date, end_date) -> list[str]` where the dates are `datetime.date`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_playlist_batch_selection.py`:

```python
"""
Which stored CSVs a batch run covers.

The date in a filename is the date the playlist was *loaded*, and that - not the
play time inside the file - is what the endpoint's range selects. Getting the
regex wrong means either missing files or sweeping in the marker object.
"""

import datetime

import pytest

import playlist_batch


def test_a_well_formed_key_parses():
    assert playlist_batch.parse_playlist_key(
        "playlist_16134_20260814_072254.csv"
    ) == ("16134", "20260814")


def test_a_slug_station_id_parses():
    assert playlist_batch.parse_playlist_key(
        "playlist_retrofm_20260814_072254.csv"
    ) == ("retrofm", "20260814")


def test_a_station_id_containing_an_underscore_parses():
    assert playlist_batch.parse_playlist_key(
        "playlist_retro_fm_20260814_072254.csv"
    ) == ("retro_fm", "20260814")


@pytest.mark.parametrize("key", [
    "processed_playlists.json",
    "playlist_16134_20260814.csv",
    "playlist_16134_2026081_072254.csv",
    "playlist__20260814_072254.csv",
    "notaplaylist.csv",
    "playlist_16134_20260814_072254.txt",
])
def test_keys_that_are_not_playlists_do_not_parse(key):
    assert playlist_batch.parse_playlist_key(key) is None


def test_parse_iso_date_accepts_the_api_format():
    assert playlist_batch.parse_iso_date("2026-08-14") == datetime.date(2026, 8, 14)


@pytest.mark.parametrize("value", ["14-08-2026", "2026/08/14", "20260814", "", "tomorrow"])
def test_parse_iso_date_rejects_anything_else(value):
    with pytest.raises(ValueError):
        playlist_batch.parse_iso_date(value)


KEYS = [
    "playlist_16134_20260813_235959.csv",
    "playlist_16134_20260814_072254.csv",
    "playlist_16134_20260815_072254.csv",
    "playlist_16134_20260816_072254.csv",
    "playlist_16134_20260817_072254.csv",
    "playlist_38225_20260815_072254.csv",
    "playlist_retrofm_20260815_072254.csv",
    "processed_playlists.json",
]


def test_both_range_endpoints_are_inclusive():
    selected = playlist_batch.select_keys(
        KEYS, "16134", datetime.date(2026, 8, 14), datetime.date(2026, 8, 16)
    )

    assert selected == [
        "playlist_16134_20260814_072254.csv",
        "playlist_16134_20260815_072254.csv",
        "playlist_16134_20260816_072254.csv",
    ]


def test_only_the_requested_station_is_selected():
    selected = playlist_batch.select_keys(
        KEYS, "38225", datetime.date(2026, 8, 15), datetime.date(2026, 8, 15)
    )

    assert selected == ["playlist_38225_20260815_072254.csv"]


def test_a_slug_station_is_selected_like_any_other():
    selected = playlist_batch.select_keys(
        KEYS, "retrofm", datetime.date(2026, 8, 15), datetime.date(2026, 8, 15)
    )

    assert selected == ["playlist_retrofm_20260815_072254.csv"]


def test_an_int_station_id_matches_a_string_key():
    selected = playlist_batch.select_keys(
        KEYS, 16134, datetime.date(2026, 8, 15), datetime.date(2026, 8, 15)
    )

    assert selected == ["playlist_16134_20260815_072254.csv"]


def test_a_range_covering_nothing_selects_nothing():
    assert playlist_batch.select_keys(
        KEYS, "16134", datetime.date(2026, 1, 1), datetime.date(2026, 1, 2)
    ) == []


def test_selection_is_sorted():
    selected = playlist_batch.select_keys(
        list(reversed(KEYS)), "16134", datetime.date(2026, 8, 13), datetime.date(2026, 8, 17)
    )

    assert selected == sorted(selected)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_playlist_batch_selection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'playlist_batch'`

- [ ] **Step 3: Write the implementation**

Create `playlist_batch.py`:

```python
"""
Turning stored playlist CSVs into Spotify playlist updates.

Filenames look like playlist_16134_20260814_072254.csv: station id, the date the
playlist was loaded, and the time it was loaded. The date range a caller supplies
selects on that load date - not on the play times inside the file - so selection
is a cheap filter over the bucket's key list rather than a download of everything.
"""

import datetime
import logging
import re

BUCKET_NAME = "radio-playlists"

# The station group is greedy, but the fixed trailing _YYYYMMDD_HHMMSS.csv anchors it,
# so a station id containing an underscore (or a slug like retrofm) still parses.
PLAYLIST_KEY_RE = re.compile(
    r"^playlist_(?P<station>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.csv$"
)


def parse_playlist_key(key):
    """(station_id, "YYYYMMDD") for a playlist CSV key, or None for anything else."""
    match = PLAYLIST_KEY_RE.match(key)
    if not match:
        return None
    return match.group("station"), match.group("date")


def parse_iso_date(value):
    """Parse a YYYY-MM-DD API date. Raises ValueError, which callers turn into a 400."""
    return datetime.datetime.strptime(value, "%Y-%m-%d").date()


def select_keys(keys, station_id, start_date, end_date):
    """
    The station's playlist keys whose load date falls in [start_date, end_date].

    Both endpoints are inclusive. `station_id` may be an int or a str; it is compared
    as a string, because ids are opaque tokens and retrofm is not a number.
    """
    station_id = str(station_id)
    start = start_date.strftime("%Y%m%d")
    end = end_date.strftime("%Y%m%d")

    selected = []
    for key in keys:
        parsed = parse_playlist_key(key)
        if not parsed:
            continue
        key_station, key_date = parsed
        if key_station != station_id:
            continue
        # YYYYMMDD is lexicographically ordered, so string comparison is a date
        # comparison here and avoids parsing every key.
        if start <= key_date <= end:
            selected.append(key)

    logging.info(
        "Selected %d playlist file(s) for station %s between %s and %s",
        len(selected), station_id, start, end
    )
    return sorted(selected)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_playlist_batch_selection.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add playlist_batch.py tests/test_playlist_batch_selection.py
git commit -m "Select stored playlist files by station and load-date range"
```

---

### Task 5: Reading tracks out of the selected CSVs

**Files:**
- Modify: `playlist_batch.py` (add imports and `collect_tracks`)
- Test: `tests/test_playlist_batch_collect.py`

**Interfaces:**
- Consumes: `playlist_batch.BUCKET_NAME` (Task 4), `playlist_upload.download_file_from_s3`
- Produces: `playlist_batch.collect_tracks(bucket, keys) -> list[tuple[str, str]]` — unique `(artist_name, song_name)` pairs in first-seen order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_playlist_batch_collect.py`:

```python
"""
Reading tracks out of the selected CSVs.

A radio station plays the same song many times a day, so the batch is deduped
before a single Spotify search is spent on it. The bucket also still holds
one-byte files from before the scrapers raised on an empty scrape, and those
must be skipped rather than crashing a run.
"""

import playlist_batch


HEADER = "time,artist_name,song_name\n"


def fake_bucket(monkeypatch, contents):
    """Serve canned CSV bodies for keys; unknown keys behave like a failed download."""
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: contents.get(key),
    )


def test_tracks_are_collected_in_order(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n2026-08-14T10:05:00,Artist B,Song B\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [
        ("Artist A", "Song A"),
        ("Artist B", "Song B"),
    ]


def test_repeats_within_a_file_are_deduped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n2026-08-14T14:00:00,Artist A,Song A\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Artist A", "Song A")]


def test_repeats_across_files_are_deduped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n",
        "b.csv": HEADER + "2026-08-15T10:00:00,Artist A,Song A\n2026-08-15T11:00:00,Artist C,Song C\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv", "b.csv"]) == [
        ("Artist A", "Song A"),
        ("Artist C", "Song C"),
    ]


def test_cyrillic_track_names_survive(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Кино,Группа крови\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Кино", "Группа крови")]


def test_an_empty_file_is_skipped(monkeypatch):
    fake_bucket(monkeypatch, {
        "empty.csv": "\n",
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["empty.csv", "a.csv"]) == [
        ("Artist A", "Song A")
    ]


def test_a_failed_download_is_skipped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["missing.csv", "a.csv"]) == [
        ("Artist A", "Song A")
    ]


def test_rows_with_a_missing_artist_or_song_are_skipped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + (
            "2026-08-14T10:00:00,,Song A\n"
            "2026-08-14T10:05:00,Artist B,\n"
            "2026-08-14T10:10:00,Artist C,Song C\n"
        ),
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Artist C", "Song C")]


def test_surrounding_whitespace_is_trimmed(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + '2026-08-14T10:00:00,"  Artist A  ","  Song A  "\n',
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Artist A", "Song A")]


def test_a_file_with_no_rows_yields_nothing(monkeypatch):
    fake_bucket(monkeypatch, {"a.csv": HEADER})

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_playlist_batch_collect.py -v`
Expected: FAIL with `AttributeError: module 'playlist_batch' has no attribute 'download_file_from_s3'`

- [ ] **Step 3: Write the implementation**

In `playlist_batch.py`, extend the imports:

```python
import datetime
import logging
import re
from io import StringIO

import pandas as pd

from playlist_upload import download_file_from_s3
```

Append `collect_tracks`:

```python
def collect_tracks(bucket, keys):
    """
    Unique (artist_name, song_name) pairs across `keys`, in first-seen order.

    Deduping here rather than later matters: a station plays the same song many times
    a day, and every unique pair costs one Spotify search. Unreadable files are
    skipped with a warning - the bucket still holds one-byte CSVs from before the
    scrapers learned to raise on an empty scrape.
    """
    seen = set()
    tracks = []

    for key in keys:
        content = download_file_from_s3(bucket, key)
        # download_file_from_s3 returns None on failure; an empty body is one of the
        # legacy one-byte files.
        if not content or not content.strip():
            logging.warning("Skipping %s: empty or could not be downloaded", key)
            continue

        try:
            frame = pd.read_csv(StringIO(content))
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            logging.warning("Skipping %s: could not parse it as CSV (%s)", key, e)
            continue

        for _, row in frame.iterrows():
            artist = row.get("artist_name")
            song = row.get("song_name")
            if pd.isna(artist) or pd.isna(song):
                continue

            pair = (str(artist).strip(), str(song).strip())
            if not pair[0] or not pair[1] or pair in seen:
                continue

            seen.add(pair)
            tracks.append(pair)

    logging.info("Collected %d unique track(s) from %d file(s)", len(tracks), len(keys))
    return tracks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_playlist_batch_collect.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add playlist_batch.py tests/test_playlist_batch_collect.py
git commit -m "Collect deduped tracks from selected playlist CSVs"
```

---

### Task 6: Upsert tracks into a named Spotify playlist

**Files:**
- Modify: `spotify_playlist.py` (add `get_playlist_tracks`, refactor `get_playlist_tracks_with_session` onto it, add `find_or_create_playlist` and `upsert_tracks_into_playlist`)
- Test: `tests/test_upsert_tracks.py`

**Interfaces:**
- Consumes: `spotify_playlist.search_track`, `start_task` / `update_task`
- Produces: `spotify_playlist.get_playlist_tracks(sp, playlist_id) -> list[dict] | None`, `find_or_create_playlist(sp, playlist_name) -> str`, `upsert_tracks_into_playlist(sp, playlist_name, tracks, task_id=None) -> dict` with keys `playlist_id`, `playlist_name`, `added`, `skipped_existing`, `unmatched` (a list of `{"artist", "song"}`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_upsert_tracks.py`:

```python
"""
Upserting tracks into a playlist that already exists and already has content.

The point of this function is that running it twice adds nothing the second time:
duplicates are dropped both within the batch and against what the playlist already
holds. Tracks Spotify cannot find are reported rather than silently dropped.
"""

import os

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import spotify_playlist


class FakeSpotify:
    """
    A minimal stand-in for spotipy.Spotify.

    `catalogue` maps "song|artist" to a URI; anything absent is a search miss.
    """

    def __init__(self, playlists=None, tracks=None, catalogue=None):
        self.playlists = playlists or []
        self.tracks = tracks or {}
        self.catalogue = catalogue or {}
        self.created = []
        self.added = []

    def current_user(self):
        return {"id": "me"}

    def user_playlists(self, user_id):
        return {"items": self.playlists, "next": None}

    def next(self, results):
        return None

    def user_playlist_create(self, user_id, name, public=False):
        playlist = {"id": f"new-{name}", "name": name, "owner": {"id": user_id}}
        self.created.append(playlist)
        self.playlists.append(playlist)
        return playlist

    def playlist_tracks(self, playlist_id):
        return {"items": self.tracks.get(playlist_id, []), "next": None}

    def playlist_add_items(self, playlist_id, uris):
        self.added.append((playlist_id, list(uris)))

    def search(self, q, type="track", limit=1):
        uri = self.catalogue.get(q)
        if not uri:
            return {"tracks": {"items": []}}
        return {"tracks": {"items": [{"uri": uri}]}}


def track_item(uri, name="n", artist="a"):
    return {"track": {"id": uri, "name": name, "uri": uri,
                      "artists": [{"name": artist}], "album": {"name": "al"}}}


def test_a_missing_playlist_is_created():
    sp = FakeSpotify(catalogue={"Song A artist:Artist A": "spotify:track:1"})

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")]
    )

    assert [p["name"] for p in sp.created] == ["Radio FM"]
    assert result["playlist_id"] == "new-Radio FM"
    assert result["added"] == 1


def test_an_existing_playlist_is_reused():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")]
    )

    assert sp.created == []
    assert result["playlist_id"] == "pl1"


def test_a_playlist_owned_by_someone_else_is_not_reused():
    # A followed playlist with the same name cannot be modified, so matching it would
    # make every add fail.
    sp = FakeSpotify(
        playlists=[{"id": "other", "name": "Radio FM", "owner": {"id": "someone-else"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")]
    )

    assert result["playlist_id"] == "new-Radio FM"


def test_tracks_already_in_the_playlist_are_skipped():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        tracks={"pl1": [track_item("spotify:track:1")]},
        catalogue={
            "Song A artist:Artist A": "spotify:track:1",
            "Song B artist:Artist B": "spotify:track:2",
        },
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A"), ("Artist B", "Song B")]
    )

    assert sp.added == [("pl1", ["spotify:track:2"])]
    assert result["added"] == 1
    assert result["skipped_existing"] == 1


def test_two_names_resolving_to_the_same_uri_are_added_once():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={
            "Song A artist:Artist A": "spotify:track:1",
            "Song A (Remastered) artist:Artist A": "spotify:track:1",
        },
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM",
        [("Artist A", "Song A"), ("Artist A", "Song A (Remastered)")],
    )

    assert sp.added == [("pl1", ["spotify:track:1"])]
    assert result["added"] == 1
    assert result["skipped_existing"] == 1


def test_unmatched_tracks_are_reported_in_full():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A"), ("Artist Z", "Song Z")]
    )

    assert result["unmatched"] == [{"artist": "Artist Z", "song": "Song Z"}]
    assert result["added"] == 1
    assert result["skipped_existing"] == 0


def test_adds_are_batched_at_the_spotify_limit_of_100():
    catalogue = {f"Song {i} artist:Artist": f"spotify:track:{i}" for i in range(250)}
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue=catalogue,
    )

    spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist", f"Song {i}") for i in range(250)]
    )

    assert [len(uris) for _, uris in sp.added] == [100, 100, 50]


def test_an_empty_batch_adds_nothing():
    sp = FakeSpotify(playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}])

    result = spotify_playlist.upsert_tracks_into_playlist(sp, "Radio FM", [])

    assert sp.added == []
    assert result == {
        "playlist_id": "pl1",
        "playlist_name": "Radio FM",
        "added": 0,
        "skipped_existing": 0,
        "unmatched": [],
    }


def test_progress_is_reported_when_a_task_id_is_given():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )
    spotify_playlist.reset_tasks()
    spotify_playlist.start_task("task-1")

    spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")], task_id="task-1"
    )

    assert spotify_playlist.get_task("task-1")["progress"] >= 75


def test_get_playlist_tracks_reads_from_a_client():
    sp = FakeSpotify(tracks={"pl1": [track_item("spotify:track:1", name="N", artist="A")]})

    tracks = spotify_playlist.get_playlist_tracks(sp, "pl1")

    assert [t["uri"] for t in tracks] == ["spotify:track:1"]
    assert tracks[0]["name"] == "N"
    assert tracks[0]["artist"] == "A"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_upsert_tracks.py -v`
Expected: FAIL with `AttributeError: module 'spotify_playlist' has no attribute 'upsert_tracks_into_playlist'`

- [ ] **Step 3: Write the implementation**

In `spotify_playlist.py`, replace the whole body of `get_playlist_tracks_with_session` with a delegation, and add the client-based version above it:

```python
def get_playlist_tracks(sp, playlist_id):
    """
    All tracks in a playlist, given an already-built client.

    The paging loop lives here rather than in get_playlist_tracks_with_session so
    callers that already hold a client - the upsert, and the nightly job - do not
    have to rebuild one from session data they may not have.
    """
    try:
        tracks = []
        results = sp.playlist_tracks(playlist_id)

        while results:
            for item in results['items']:
                track = item['track']
                if track:  # Handle deleted tracks
                    tracks.append({
                        'id': track['id'],
                        'name': track['name'],
                        'artist': track['artists'][0]['name'] if track['artists'] else '',
                        'uri': track['uri'],
                        'album': track['album']['name'] if track['album'] else ''
                    })

            if results['next']:
                results = sp.next(results)
            else:
                break

        logging.info(f"Retrieved {len(tracks)} tracks from playlist {playlist_id}")
        return tracks

    except Exception as e:
        logging.error(f"Error getting playlist tracks: {e}")
        return None


def get_playlist_tracks_with_session(playlist_id, session_data):
    """
    Get all tracks from a specific playlist using provided session data
    """
    sp = create_spotify_client_with_session(session_data)
    if not sp:
        logging.error("Failed to create Spotify client for getting playlist tracks")
        return None

    return get_playlist_tracks(sp, playlist_id)
```

Then append `find_or_create_playlist` and `upsert_tracks_into_playlist` after
`get_playlist_tracks_with_session`:

```python
def find_or_create_playlist(sp, playlist_name):
    """
    The id of the user's own playlist with this exact name, creating it if absent.

    Only playlists the user owns are considered: a followed playlist that happens to
    share the name cannot be modified, so matching it would make every add fail.
    Names are not unique, so if several match the first is used and the ambiguity is
    logged rather than guessed at silently.
    """
    user_id = sp.current_user()['id']

    matches = []
    results = sp.user_playlists(user_id)
    while results:
        for item in results['items']:
            if item['name'] == playlist_name and item['owner']['id'] == user_id:
                matches.append(item['id'])
        if results.get('next'):
            results = sp.next(results)
        else:
            break

    if matches:
        if len(matches) > 1:
            logging.warning(
                "%d playlists are named '%s'; using %s. Rename the others to remove "
                "the ambiguity.", len(matches), playlist_name, matches[0]
            )
        return matches[0]

    playlist = sp.user_playlist_create(user_id, playlist_name, public=False)
    logging.info(f"Created playlist '{playlist_name}' ({playlist['id']})")
    return playlist['id']


def upsert_tracks_into_playlist(sp, playlist_name, tracks, task_id=None):
    """
    Add `tracks` to the named playlist, skipping anything already in it.

    `tracks` is a sequence of (artist, song) pairs, already deduped by the caller.
    Running this twice over the same input adds nothing the second time: URIs are
    checked against what the playlist already holds and against each other, so two
    different track names that resolve to the same recording are added once.

    `task_id` is optional - update_task is a no-op for an unknown id, so the nightly
    job can call this without registering a task at all.

    Returns {"playlist_id", "playlist_name", "added", "skipped_existing", "unmatched"}.
    """
    update_task(task_id, progress=5, message=f"Resolving playlist '{playlist_name}'...")
    playlist_id = find_or_create_playlist(sp, playlist_name)

    update_task(task_id, progress=10, message='Reading tracks already in the playlist...')
    existing = get_playlist_tracks(sp, playlist_id) or []
    known_uris = {track['uri'] for track in existing}

    new_uris = []
    unmatched = []
    total = len(tracks)

    for index, (artist, song) in enumerate(tracks):
        # 10-70% is the search phase, matching create_playlist_from_csv's shape.
        update_task(
            task_id,
            progress=10 + int(index / total * 60) if total else 10,
            message=f'Searching for track: {song} by {artist}'
        )

        uri = search_track(sp, artist, song)
        if not uri:
            unmatched.append({'artist': artist, 'song': song})
            continue
        if uri in known_uris:
            continue

        known_uris.add(uri)
        new_uris.append(uri)

    skipped_existing = total - len(new_uris) - len(unmatched)

    update_task(
        task_id, progress=75,
        message=f'Adding {len(new_uris)} new track(s) to {playlist_name}...'
    )

    batch_size = 100  # Spotify API limit
    for i in range(0, len(new_uris), batch_size):
        batch = new_uris[i:i + batch_size]
        sp.playlist_add_items(playlist_id, batch)
        update_task(
            task_id,
            progress=75 + int(i / len(new_uris) * 20),
            message=f'Adding tracks {i + 1} to {min(i + batch_size, len(new_uris))}'
        )

    logging.info(
        "Upsert into '%s': %d added, %d already present, %d not found on Spotify",
        playlist_name, len(new_uris), skipped_existing, len(unmatched)
    )

    return {
        'playlist_id': playlist_id,
        'playlist_name': playlist_name,
        'added': len(new_uris),
        'skipped_existing': skipped_existing,
        'unmatched': unmatched,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_upsert_tracks.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the whole suite — `get_playlist_tracks_with_session` was refactored**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add spotify_playlist.py tests/test_upsert_tracks.py
git commit -m "Upsert deduped tracks into a Spotify playlist resolved by name"
```

---

### Task 7: Writing objects to S3 and the processed-files marker

**Files:**
- Modify: `playlist_upload.py` (add `put_object_to_s3`)
- Modify: `playlist_batch.py` (add `PROCESSED_MARKER_KEY`, `load_processed_keys`, `save_processed_keys`)
- Test: `tests/test_processed_marker.py`

**Interfaces:**
- Consumes: `playlist_batch.BUCKET_NAME` (Task 4)
- Produces: `playlist_upload.put_object_to_s3(bucket_name, object_name, content) -> bool`; `playlist_batch.PROCESSED_MARKER_KEY` (`"processed_playlists.json"`), `load_processed_keys(bucket) -> set[str] | None` (**None means the marker could not be read**, distinct from an empty set), `save_processed_keys(keys, bucket) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_processed_marker.py`:

```python
"""
The record of which stored CSVs have already been pushed to Spotify.

The distinction that matters here is between "the marker says nothing has been
processed" and "the marker could not be read". The first means process everything;
the second must not, because silently treating an S3 read failure as an empty set
would re-scan the whole bucket and then overwrite the real record with a partial one.
"""

import json

import playlist_batch


def test_the_marker_key_is_not_mistaken_for_a_playlist():
    assert playlist_batch.parse_playlist_key(playlist_batch.PROCESSED_MARKER_KEY) is None


def test_a_valid_marker_is_read(monkeypatch):
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: json.dumps({"processed": ["a.csv", "b.csv"], "updated_at": "x"}),
    )

    assert playlist_batch.load_processed_keys("bucket") == {"a.csv", "b.csv"}


def test_an_unreadable_marker_is_distinguished_from_an_empty_one(monkeypatch):
    monkeypatch.setattr(playlist_batch, "download_file_from_s3", lambda bucket, key: None)

    assert playlist_batch.load_processed_keys("bucket") is None


def test_an_empty_marker_reads_as_an_empty_set(monkeypatch):
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: json.dumps({"processed": [], "updated_at": "x"}),
    )

    assert playlist_batch.load_processed_keys("bucket") == set()


def test_a_malformed_marker_reads_as_unreadable(monkeypatch):
    monkeypatch.setattr(playlist_batch, "download_file_from_s3", lambda bucket, key: "{not json")

    assert playlist_batch.load_processed_keys("bucket") is None


def test_a_marker_with_a_wrong_shape_reads_as_unreadable(monkeypatch):
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: json.dumps({"processed": "a.csv"}),
    )

    assert playlist_batch.load_processed_keys("bucket") is None


def test_saving_writes_sorted_keys_and_a_timestamp(monkeypatch):
    written = {}
    monkeypatch.setattr(
        playlist_batch, "put_object_to_s3",
        lambda bucket, key, content: written.update(bucket=bucket, key=key, content=content) or True,
    )

    assert playlist_batch.save_processed_keys({"b.csv", "a.csv"}, "bucket") is True

    assert written["key"] == playlist_batch.PROCESSED_MARKER_KEY
    payload = json.loads(written["content"])
    assert payload["processed"] == ["a.csv", "b.csv"]
    assert payload["updated_at"]


def test_saving_reports_an_s3_failure(monkeypatch):
    monkeypatch.setattr(playlist_batch, "put_object_to_s3", lambda bucket, key, content: False)

    assert playlist_batch.save_processed_keys({"a.csv"}, "bucket") is False
```

Create `tests/test_put_object.py`:

```python
"""
put_object_to_s3 follows playlist_upload's swallow-and-return-bool contract.
"""

import playlist_upload


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def put_object(self, Bucket, Key, Body):
        if self.fail:
            raise RuntimeError("s3 is down")
        self.calls.append((Bucket, Key, Body))


def test_a_string_body_is_written_as_utf8(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(playlist_upload.boto3, "client", lambda *a, **k: client)

    assert playlist_upload.put_object_to_s3("bucket", "key.json", "Группа") is True
    assert client.calls == [("bucket", "key.json", "Группа".encode("utf-8"))]


def test_a_failure_is_reported_by_return_value_not_raised(monkeypatch):
    monkeypatch.setattr(playlist_upload.boto3, "client", lambda *a, **k: FakeClient(fail=True))

    assert playlist_upload.put_object_to_s3("bucket", "key.json", "x") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_processed_marker.py tests/test_put_object.py -v`
Expected: FAIL — `playlist_upload` has no `put_object_to_s3`, `playlist_batch` has no `PROCESSED_MARKER_KEY`.

- [ ] **Step 3: Write the implementation**

Append to `playlist_upload.py`:

```python
def put_object_to_s3(bucket_name, object_name, content):
    """
    Write `content` straight to a key, without a local file first.

    upload_file_to_s3 takes a path, which is wrong for small generated documents like
    the processed-files marker. Same contract as everything else here: exceptions are
    swallowed and the return value is the caller's only signal.
    """
    try:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )

        body = content.encode("utf-8") if isinstance(content, str) else content
        s3_client.put_object(Bucket=bucket_name, Key=object_name, Body=body)

        logging.info(f"Object '{object_name}' successfully written to bucket '{bucket_name}'.")
        return True

    except ClientError as e:
        logging.error(f"Error writing object '{object_name}' to bucket '{bucket_name}': {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error writing '{object_name}' to '{bucket_name}': {e}")
        return False
```

In `playlist_batch.py`, extend the imports and add the marker functions:

```python
import json
import threading

from playlist_upload import download_file_from_s3, put_object_to_s3
```

```python
# Which stored CSVs have already been pushed to Spotify by the nightly job. Kept in
# the bucket rather than on the volume so it survives a container being recreated.
PROCESSED_MARKER_KEY = "processed_playlists.json"

# uWSGI runs a single process (-p 1 --threads 8), so a module-level lock is enough to
# keep two overlapping nightly runs from interleaving a read and a write.
_marker_lock = threading.Lock()


def load_processed_keys(bucket=BUCKET_NAME):
    """
    The set of already-processed keys, or **None** when the marker cannot be read.

    The difference matters. An empty set means "process everything", which is right
    on a first run. None means S3 said nothing usable, and treating that as an empty
    set would re-scan the whole bucket and then overwrite the real record.
    """
    content = download_file_from_s3(bucket, PROCESSED_MARKER_KEY)
    if content is None:
        return None

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        logging.error("The processed marker in %s is not valid JSON: %s", bucket, e)
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("processed"), list):
        logging.error("The processed marker in %s does not have the expected shape", bucket)
        return None

    return {key for key in payload["processed"] if isinstance(key, str)}


def save_processed_keys(keys, bucket=BUCKET_NAME):
    """Write the processed-keys marker. Returns False if S3 rejected it."""
    payload = {
        "processed": sorted(keys),
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    return put_object_to_s3(bucket, PROCESSED_MARKER_KEY, json.dumps(payload, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_processed_marker.py tests/test_put_object.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add playlist_upload.py playlist_batch.py tests/test_processed_marker.py tests/test_put_object.py
git commit -m "Track which playlist files have reached Spotify in an S3 marker"
```

---

### Task 8: The on-demand range batch

**Files:**
- Modify: `playlist_batch.py` (add `import station_playlists`, `import spotify_playlist`, and `run_range_batch`)
- Test: `tests/test_run_range_batch.py`

**Interfaces:**
- Consumes: `station_playlists.get_playlist_name` (Task 1), `select_keys` (Task 4), `collect_tracks` (Task 5), `spotify_playlist.upsert_tracks_into_playlist` and `create_spotify_client_with_session` (Task 6)
- Produces: `playlist_batch.run_range_batch(station_id, start_date, end_date, task_id, session_data, bucket=BUCKET_NAME) -> bool`, where the dates are `datetime.date`. The task's `result` field carries the upsert result plus a `files` count.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_range_batch.py`:

```python
"""
The job behind POST /create-playlist-batch.

Everything the caller learns comes from the task store, so what matters is that
each outcome - no files, no tracks, no auth, an exception - leaves a terminal
status and a message that says which one happened.
"""

import datetime
import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import playlist_batch
import spotify_playlist
import station_playlists


START = datetime.date(2026, 8, 14)
END = datetime.date(2026, 8, 16)

KEYS = [
    "playlist_16134_20260814_072254.csv",
    "playlist_16134_20260815_072254.csv",
    "processed_playlists.json",
]


@pytest.fixture
def batch(monkeypatch):
    """Stub out S3, the config and Spotify; capture what the upsert was given."""
    spotify_playlist.reset_tasks()
    state = {
        "tracks": [("Artist A", "Song A")],
        "upsert_calls": [],
        "client": object(),
    }

    # playlist_batch.station_playlists IS station_playlists, so one patch covers both.
    monkeypatch.setattr(station_playlists, "get_playlist_name",
                        lambda station_id: "Radio FM" if str(station_id) == "16134" else None)
    monkeypatch.setattr(playlist_batch, "list_objects_in_bucket", lambda bucket: KEYS)
    monkeypatch.setattr(playlist_batch, "collect_tracks", lambda bucket, keys: state["tracks"])
    monkeypatch.setattr(spotify_playlist, "create_spotify_client_with_session",
                        lambda session_data: state["client"])

    def fake_upsert(sp, playlist_name, tracks, task_id=None):
        state["upsert_calls"].append((playlist_name, list(tracks)))
        return {"playlist_id": "pl1", "playlist_name": playlist_name,
                "added": len(tracks), "skipped_existing": 0, "unmatched": []}

    monkeypatch.setattr(spotify_playlist, "upsert_tracks_into_playlist", fake_upsert)
    return state


def test_a_successful_run_upserts_and_completes(batch):
    assert playlist_batch.run_range_batch("16134", START, END, "t1", {}) is True

    assert batch["upsert_calls"] == [("Radio FM", [("Artist A", "Song A")])]
    task = spotify_playlist.get_task("t1")
    assert task["status"] == "completed"
    assert task["progress"] == 100
    assert task["result"]["added"] == 1
    assert task["result"]["files"] == 2


def test_an_unconfigured_station_fails_the_task(batch):
    assert playlist_batch.run_range_batch("99999", START, END, "t1", {}) is False

    task = spotify_playlist.get_task("t1")
    assert task["status"] == "error"
    assert "not configured" in task["message"]


def test_no_spotify_client_fails_the_task(batch, monkeypatch):
    monkeypatch.setattr(spotify_playlist, "create_spotify_client_with_session",
                        lambda session_data: None)

    assert playlist_batch.run_range_batch("16134", START, END, "t1", {}) is False

    task = spotify_playlist.get_task("t1")
    assert task["status"] == "error"
    assert "authenticat" in task["message"].lower()


def test_a_range_with_no_files_completes_without_touching_spotify(batch):
    assert playlist_batch.run_range_batch(
        "16134", datetime.date(2026, 1, 1), datetime.date(2026, 1, 2), "t1", {}
    ) is True

    assert batch["upsert_calls"] == []
    task = spotify_playlist.get_task("t1")
    assert task["status"] == "completed"
    assert task["result"]["files"] == 0
    assert task["result"]["added"] == 0


def test_files_containing_no_tracks_complete_without_touching_spotify(batch):
    batch["tracks"] = []

    assert playlist_batch.run_range_batch("16134", START, END, "t1", {}) is True

    assert batch["upsert_calls"] == []
    assert spotify_playlist.get_task("t1")["status"] == "completed"


def test_an_unexpected_error_fails_the_task_rather_than_escaping(batch, monkeypatch):
    def boom(sp, playlist_name, tracks, task_id=None):
        raise RuntimeError("spotify exploded")

    monkeypatch.setattr(spotify_playlist, "upsert_tracks_into_playlist", boom)

    assert playlist_batch.run_range_batch("16134", START, END, "t1", {}) is False

    task = spotify_playlist.get_task("t1")
    assert task["status"] == "error"
    assert "spotify exploded" in task["message"]


def test_the_endpoint_never_writes_the_processed_marker(batch, monkeypatch):
    # A manual run is idempotent through dedupe; marking those files processed would
    # silently exclude them from a later nightly run.
    monkeypatch.setattr(playlist_batch, "save_processed_keys",
                        lambda keys, bucket=None: pytest.fail("marker must not be written"))

    playlist_batch.run_range_batch("16134", START, END, "t1", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_range_batch.py -v`
Expected: FAIL with `AttributeError: module 'playlist_batch' has no attribute 'list_objects_in_bucket'`

- [ ] **Step 3: Write the implementation**

In `playlist_batch.py`, extend the imports:

```python
from playlist_upload import download_file_from_s3, list_objects_in_bucket, put_object_to_s3

import spotify_playlist
import station_playlists
```

Append `run_range_batch`:

```python
def run_range_batch(station_id, start_date, end_date, task_id, session_data, bucket=BUCKET_NAME):
    """
    Upsert every stored CSV for a station between two load dates into its playlist.

    Runs in a daemon thread behind POST /create-playlist-batch, so it never raises:
    the caller only ever sees the task store, and an escaping exception would leave
    the task stuck at "processing" forever.

    Deliberately does not write the processed marker - see save_processed_keys.
    """
    try:
        spotify_playlist.start_task(task_id, message='Selecting playlist files...')

        playlist_name = station_playlists.get_playlist_name(station_id)
        if not playlist_name:
            spotify_playlist.update_task(
                task_id, status='error',
                message=f"Station {station_id} is not configured in station_playlists.json"
            )
            return False

        sp = spotify_playlist.create_spotify_client_with_session(session_data)
        if not sp:
            spotify_playlist.update_task(
                task_id, status='error',
                message='Not authenticated with Spotify. Connect your account and try again.'
            )
            return False

        keys = select_keys(list_objects_in_bucket(bucket), station_id, start_date, end_date)
        empty_result = {
            'playlist_name': playlist_name,
            'added': 0,
            'skipped_existing': 0,
            'unmatched': [],
            'files': len(keys),
        }

        if not keys:
            spotify_playlist.update_task(
                task_id, status='completed', progress=100,
                message=(f"No playlist files for station {station_id} between "
                         f"{start_date} and {end_date}"),
                result=empty_result
            )
            return True

        spotify_playlist.update_task(
            task_id, progress=3, message=f'Reading {len(keys)} playlist file(s)...'
        )
        tracks = collect_tracks(bucket, keys)

        if not tracks:
            spotify_playlist.update_task(
                task_id, status='completed', progress=100,
                message=f"The {len(keys)} selected file(s) contained no usable tracks",
                result=empty_result
            )
            return True

        result = spotify_playlist.upsert_tracks_into_playlist(
            sp, playlist_name, tracks, task_id
        )
        result['files'] = len(keys)

        spotify_playlist.update_task(
            task_id, status='completed', progress=100,
            message=(f"Added {result['added']} track(s) to '{playlist_name}' - "
                     f"{result['skipped_existing']} already there, "
                     f"{len(result['unmatched'])} not found on Spotify"),
            result=result
        )
        return True

    except Exception as e:
        logging.error(f"Error running batch for station {station_id}: {e}")
        spotify_playlist.update_task(
            task_id, status='error', message=f'Error running batch: {str(e)}'
        )
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_range_batch.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add playlist_batch.py tests/test_run_range_batch.py
git commit -m "Add the on-demand station date-range batch"
```

---

### Task 9: The nightly new-files batch

**Files:**
- Modify: `playlist_batch.py` (add `process_new_playlists`)
- Test: `tests/test_process_new_playlists.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 3, 4, 5, 6, 7
- Produces: `playlist_batch.process_new_playlists(bucket=BUCKET_NAME) -> dict` with keys `processed` (list of keys newly recorded) and `failures` (list of `(source, reason)` tuples, matching `scrape_and_upload_playlists`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_process_new_playlists.py`:

```python
"""
The nightly push of newly scraped files into Spotify.

The rule that carries the whole design: a key is recorded as processed only when
the upsert that consumed it succeeded. A Spotify outage must leave those files to
be retried tomorrow, not swallow them - which is the same failure mode as counting
a playlist as uploaded when S3 rejected it.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import playlist_batch
import spotify_playlist
import station_playlists


KEYS = [
    "playlist_16134_20260814_072254.csv",
    "playlist_38225_20260814_072300.csv",
    "playlist_99999_20260814_072400.csv",
    "processed_playlists.json",
]


@pytest.fixture
def nightly(monkeypatch):
    state = {
        "processed": set(),
        "saved": None,
        "save_succeeds": True,
        "upserts": [],
        "fail_stations": set(),
    }

    monkeypatch.setattr(playlist_batch.station_playlists, "get_configured_stations",
                        lambda: {"16134": "Radio FM", "38225": "Hit FM"})
    monkeypatch.setattr(playlist_batch, "list_objects_in_bucket", lambda bucket: KEYS)
    monkeypatch.setattr(playlist_batch, "collect_tracks",
                        lambda bucket, keys: [("Artist", "Song")])
    monkeypatch.setattr(playlist_batch, "load_processed_keys",
                        lambda bucket=None: state["processed"])
    monkeypatch.setattr(spotify_playlist, "create_spotify_client_from_store",
                        lambda: object())

    def fake_save(keys, bucket=None):
        state["saved"] = set(keys)
        return state["save_succeeds"]

    def fake_upsert(sp, playlist_name, tracks, task_id=None):
        if playlist_name in state["fail_stations"]:
            raise RuntimeError(f"{playlist_name} exploded")
        state["upserts"].append(playlist_name)
        return {"playlist_id": "pl", "playlist_name": playlist_name,
                "added": 1, "skipped_existing": 0, "unmatched": []}

    monkeypatch.setattr(playlist_batch, "save_processed_keys", fake_save)
    monkeypatch.setattr(spotify_playlist, "upsert_tracks_into_playlist", fake_upsert)
    return state


def test_new_files_for_configured_stations_are_processed(nightly):
    outcome = playlist_batch.process_new_playlists()

    assert sorted(nightly["upserts"]) == ["Hit FM", "Radio FM"]
    assert sorted(outcome["processed"]) == [
        "playlist_16134_20260814_072254.csv",
        "playlist_38225_20260814_072300.csv",
    ]
    assert outcome["failures"] == []


def test_unconfigured_stations_and_the_marker_are_ignored(nightly):
    outcome = playlist_batch.process_new_playlists()

    assert "playlist_99999_20260814_072400.csv" not in outcome["processed"]
    assert "processed_playlists.json" not in outcome["processed"]


def test_already_processed_files_are_not_reprocessed(nightly):
    nightly["processed"] = {"playlist_16134_20260814_072254.csv"}

    outcome = playlist_batch.process_new_playlists()

    assert nightly["upserts"] == ["Hit FM"]
    assert outcome["processed"] == ["playlist_38225_20260814_072300.csv"]


def test_the_marker_is_saved_as_the_union_of_old_and_new(nightly):
    nightly["processed"] = {"playlist_16134_20260813_010101.csv"}

    playlist_batch.process_new_playlists()

    assert nightly["saved"] == {
        "playlist_16134_20260813_010101.csv",
        "playlist_16134_20260814_072254.csv",
        "playlist_38225_20260814_072300.csv",
    }


def test_a_failing_station_is_not_marked_processed_and_does_not_stop_the_others(nightly):
    nightly["fail_stations"] = {"Radio FM"}

    outcome = playlist_batch.process_new_playlists()

    assert nightly["upserts"] == ["Hit FM"]
    assert outcome["processed"] == ["playlist_38225_20260814_072300.csv"]
    assert nightly["saved"] == {"playlist_38225_20260814_072300.csv"}
    assert [source for source, _ in outcome["failures"]] == ["16134"]


def test_no_stored_token_is_a_reported_no_op(nightly, monkeypatch):
    monkeypatch.setattr(spotify_playlist, "create_spotify_client_from_store", lambda: None)

    outcome = playlist_batch.process_new_playlists()

    assert outcome["processed"] == []
    assert outcome["failures"] == [("spotify", "no stored Spotify token")]
    assert nightly["upserts"] == []


def test_no_configured_stations_is_a_no_op(nightly, monkeypatch):
    monkeypatch.setattr(playlist_batch.station_playlists, "get_configured_stations", dict)

    outcome = playlist_batch.process_new_playlists()

    assert outcome == {"processed": [], "failures": []}


def test_an_unreadable_marker_bootstraps_an_empty_one(nightly, monkeypatch):
    monkeypatch.setattr(playlist_batch, "load_processed_keys", lambda bucket=None: None)

    outcome = playlist_batch.process_new_playlists()

    assert sorted(nightly["upserts"]) == ["Hit FM", "Radio FM"]
    assert len(outcome["processed"]) == 2


def test_an_unwritable_marker_aborts_before_touching_spotify(nightly, monkeypatch):
    # If S3 will not take the marker, running anyway means reprocessing the whole
    # bucket again tomorrow, and every night after that.
    monkeypatch.setattr(playlist_batch, "load_processed_keys", lambda bucket=None: None)
    nightly["save_succeeds"] = False

    outcome = playlist_batch.process_new_playlists()

    assert nightly["upserts"] == []
    assert outcome["processed"] == []
    assert [source for source, _ in outcome["failures"]] == ["marker"]


def test_a_marker_save_failure_after_a_run_is_reported(nightly):
    nightly["save_succeeds"] = False

    outcome = playlist_batch.process_new_playlists()

    assert len(outcome["processed"]) == 2
    assert ("marker", "could not save the processed marker") in outcome["failures"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_new_playlists.py -v`
Expected: FAIL with `AttributeError: module 'playlist_batch' has no attribute 'process_new_playlists'`

- [ ] **Step 3: Write the implementation**

Append to `playlist_batch.py`:

```python
def process_new_playlists(bucket=BUCKET_NAME):
    """
    Push every not-yet-processed CSV for a configured station into its playlist.

    Runs unattended from the nightly scheduler, so it authenticates from the stored
    token rather than a session and never raises: a failure here must not stop the
    scrape's own result from being logged.

    A key is recorded as processed only once the upsert that consumed it succeeded,
    so a Spotify outage leaves those files for tomorrow instead of swallowing them.

    Returns {"processed": [keys], "failures": [(source, reason)]}, shaped like
    scrape_and_upload_playlists so both halves of the nightly job read the same way.
    """
    stations = station_playlists.get_configured_stations()
    if not stations:
        logging.warning("No stations configured - nothing to push to Spotify")
        return {'processed': [], 'failures': []}

    sp = spotify_playlist.create_spotify_client_from_store()
    if not sp:
        logging.warning(
            "Skipping the nightly Spotify push: no stored token. Connect a Spotify "
            "account once to arm it."
        )
        return {'processed': [], 'failures': [('spotify', 'no stored Spotify token')]}

    with _marker_lock:
        processed = load_processed_keys(bucket)
        if processed is None:
            # Either the marker has never existed or S3 would not serve it. Write an
            # empty one: if that succeeds the marker simply did not exist yet, and if
            # it fails S3 is broken and running anyway would reprocess the whole
            # bucket tonight and every night after.
            logging.warning("No usable processed marker in %s - bootstrapping one", bucket)
            if not save_processed_keys(set(), bucket):
                logging.error(
                    "Could not read or write the processed marker in %s - skipping the "
                    "nightly push rather than reprocessing the whole bucket", bucket
                )
                return {
                    'processed': [],
                    'failures': [('marker', 'could not read or write the processed marker')],
                }
            processed = set()

        pending = {}
        for key in list_objects_in_bucket(bucket):
            if key in processed:
                continue
            parsed = parse_playlist_key(key)
            if not parsed:
                continue
            station_id, _ = parsed
            if station_id not in stations:
                continue
            pending.setdefault(station_id, []).append(key)

        newly_processed = []
        failures = []

        for station_id, station_keys in sorted(pending.items()):
            station_keys = sorted(station_keys)
            try:
                tracks = collect_tracks(bucket, station_keys)
                if tracks:
                    spotify_playlist.upsert_tracks_into_playlist(
                        sp, stations[station_id], tracks
                    )
                else:
                    # Nothing usable in them, and there never will be - mark them done
                    # so they are not re-downloaded every night forever.
                    logging.warning(
                        "Station %s: %d new file(s) contained no usable tracks",
                        station_id, len(station_keys)
                    )
                newly_processed.extend(station_keys)
            except Exception as e:
                failures.append((station_id, str(e)))
                logging.error("Error pushing station %s to Spotify: %s", station_id, e)

        if newly_processed and not save_processed_keys(processed | set(newly_processed), bucket):
            failures.append(('marker', 'could not save the processed marker'))

        logging.info(
            "Nightly Spotify push: %d file(s) processed, %d failure(s)",
            len(newly_processed), len(failures)
        )
        return {'processed': newly_processed, 'failures': failures}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_new_playlists.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add playlist_batch.py tests/test_process_new_playlists.py
git commit -m "Push newly scraped playlist files to Spotify unattended"
```

---

### Task 10: Routes and the scheduled job

**Files:**
- Modify: `app.py` (add `import playlist_batch`, `import station_playlists`; add `create_playlist_batch` and `api_station_playlists` routes; add `result` to `playlist_progress`; extend `my_scheduled_job`)
- Test: `tests/test_batch_route.py`

**Interfaces:**
- Consumes: `playlist_batch.parse_iso_date`, `run_range_batch`, `process_new_playlists`; `station_playlists.get_playlist_name`, `get_configured_stations`
- Produces: `POST /create-playlist-batch`, `GET /api/station-playlists`, and a nullable `result` key on `GET /playlist_progress/<task_id>`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_route.py`:

```python
"""
Validation and wiring for POST /create-playlist-batch.

The route's whole job is to reject bad input with a 400 the caller can read, refuse
unauthenticated callers with the same 401 shape every other Spotify route uses, and
hand a dict(session) copy - not the live session - to the thread.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app
import spotify_playlist
import station_playlists


TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at": 9999999999}


@pytest.fixture
def client(monkeypatch):
    flask_app.app.config["TESTING"] = True
    monkeypatch.setattr(flask_app.station_playlists, "get_playlist_name",
                        lambda station_id: "Radio FM" if str(station_id) == "16134" else None)
    monkeypatch.setattr(flask_app.station_playlists, "get_configured_stations",
                        lambda: {"16134": "Radio FM"})
    with flask_app.app.test_client() as client:
        yield client


@pytest.fixture
def authed(client):
    with client.session_transaction() as session:
        session["spotify_token_info"] = TOKEN
    return client


@pytest.fixture
def started(monkeypatch):
    """Capture the arguments run_range_batch is called with instead of running it."""
    calls = []
    monkeypatch.setattr(flask_app.playlist_batch, "run_range_batch",
                        lambda *args, **kwargs: calls.append(args) or True)
    return calls


def post(client, **body):
    return client.post("/create-playlist-batch", json=body)


def test_a_valid_request_starts_a_task(authed, started):
    response = post(authed, station_id="16134", start_date="2026-08-14", end_date="2026-08-16")

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["task_id"]


def test_the_thread_receives_a_session_copy_not_the_live_session(authed, started):
    post(authed, station_id="16134", start_date="2026-08-14", end_date="2026-08-16")

    session_data = started[0][4]
    assert type(session_data) is dict
    assert session_data["spotify_token_info"] == TOKEN


def test_a_missing_body_is_rejected(authed):
    response = authed.post("/create-playlist-batch")

    assert response.status_code == 400


@pytest.mark.parametrize("body", [
    {"start_date": "2026-08-14", "end_date": "2026-08-16"},
    {"station_id": "16134", "end_date": "2026-08-16"},
    {"station_id": "16134", "start_date": "2026-08-14"},
    {"station_id": "", "start_date": "2026-08-14", "end_date": "2026-08-16"},
])
def test_missing_fields_are_rejected(authed, body):
    assert authed.post("/create-playlist-batch", json=body).status_code == 400


def test_an_unconfigured_station_is_rejected(authed):
    response = post(authed, station_id="99999", start_date="2026-08-14", end_date="2026-08-16")

    assert response.status_code == 400
    assert "not configured" in response.json["message"]


@pytest.mark.parametrize("start,end", [
    ("14-08-2026", "2026-08-16"),
    ("2026-08-14", "16/08/2026"),
    ("2026-13-01", "2026-08-16"),
])
def test_dates_that_are_not_iso_are_rejected(authed, start, end):
    response = post(authed, station_id="16134", start_date=start, end_date=end)

    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.json["message"]


def test_a_reversed_range_is_rejected(authed):
    response = post(authed, station_id="16134", start_date="2026-08-16", end_date="2026-08-14")

    assert response.status_code == 400
    assert "after" in response.json["message"]


def test_a_single_day_range_is_accepted(authed, started):
    response = post(authed, station_id="16134", start_date="2026-08-14", end_date="2026-08-14")

    assert response.status_code == 200


def test_an_unauthenticated_caller_gets_the_standard_401(client, started):
    response = post(client, station_id="16134", start_date="2026-08-14", end_date="2026-08-16")

    assert response.status_code == 401
    assert response.json["auth_url"] == "/spotify/auth"
    assert started == []


def test_the_station_list_is_served(client):
    response = client.get("/api/station-playlists")

    assert response.status_code == 200
    assert response.json == {"status": "success", "stations": {"16134": "Radio FM"}}


def test_progress_carries_the_result_when_a_task_has_one(client):
    spotify_playlist.reset_tasks()
    spotify_playlist.start_task("t1")
    spotify_playlist.update_task(
        "t1", status="completed", progress=100, message="done",
        result={"added": 3, "skipped_existing": 1, "unmatched": [], "files": 2}
    )

    response = client.get("/playlist_progress/t1")

    assert response.json["result"]["added"] == 3


def test_progress_carries_a_null_result_for_a_task_without_one(client):
    spotify_playlist.reset_tasks()
    spotify_playlist.start_task("t2")

    response = client.get("/playlist_progress/t2")

    assert response.json["result"] is None
```

Create `tests/test_scheduled_job_pushes_to_spotify.py`:

```python
"""
The nightly job scrapes and then pushes, and neither half can silence the other.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app


def test_the_push_runs_after_a_successful_scrape(monkeypatch):
    calls = []
    monkeypatch.setattr(flask_app, "scrape_and_upload_playlists",
                        lambda: calls.append("scrape") or (["a.csv"], []))
    monkeypatch.setattr(flask_app.playlist_batch, "process_new_playlists",
                        lambda: calls.append("push") or {"processed": ["a.csv"], "failures": []})

    flask_app.my_scheduled_job()

    assert calls == ["scrape", "push"]


def test_a_failing_push_does_not_hide_the_scrape_result(monkeypatch, caplog):
    monkeypatch.setattr(flask_app, "scrape_and_upload_playlists", lambda: (["a.csv"], []))

    def boom():
        raise RuntimeError("spotify exploded")

    monkeypatch.setattr(flask_app.playlist_batch, "process_new_playlists", boom)

    with caplog.at_level("INFO"):
        flask_app.my_scheduled_job()

    assert "uploaded 1 playlist(s)" in caplog.text
    assert "spotify exploded" in caplog.text


def test_a_failing_scrape_does_not_stop_the_push(monkeypatch):
    # Files uploaded on a previous night may still be unprocessed.
    calls = []

    def boom():
        raise RuntimeError("scrape exploded")

    monkeypatch.setattr(flask_app, "scrape_and_upload_playlists", boom)
    monkeypatch.setattr(flask_app.playlist_batch, "process_new_playlists",
                        lambda: calls.append("push") or {"processed": [], "failures": []})

    flask_app.my_scheduled_job()

    assert calls == ["push"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_batch_route.py tests/test_scheduled_job_pushes_to_spotify.py -v`
Expected: FAIL — `app` has no attribute `station_playlists`, and `/create-playlist-batch` 404s.

- [ ] **Step 3: Write the implementation**

In `app.py`, add to the imports after `import spotify_playlist`:

```python
import playlist_batch
import station_playlists
```

Replace `my_scheduled_job` with:

```python
def my_scheduled_job():
    """
    Scheduled job: scrape into S3, then push anything not yet in Spotify.

    The two halves are independently guarded. A Spotify outage must not hide the
    scrape's result, and files uploaded on an earlier night may still be waiting,
    so a failing scrape must not skip the push.
    """
    try:
        uploaded, failures = scrape_and_upload_playlists()
        if failures:
            logging.error(
                f"Scheduled playlist loading finished with {len(failures)} failure(s): {failures}"
            )
        logging.info(f"Scheduled playlist loading uploaded {len(uploaded)} playlist(s)")
    except Exception as e:
        logging.error(f"Error in scheduled playlist loading: {e}")

    try:
        outcome = playlist_batch.process_new_playlists()
        if outcome['failures']:
            logging.error(
                f"Nightly Spotify push finished with {len(outcome['failures'])} "
                f"failure(s): {outcome['failures']}"
            )
        logging.info(
            f"Nightly Spotify push processed {len(outcome['processed'])} playlist file(s)"
        )
    except Exception as e:
        logging.error(f"Error in the nightly Spotify push: {e}")
```

Replace the body of `playlist_progress` with:

```python
@app.route('/playlist_progress/<task_id>')
def playlist_progress(task_id):
    """Get the progress of a playlist creation task"""
    task = spotify_playlist.get_task(task_id)
    if not task:
        return {
            'status': 'error',
            'message': 'Task not found'
        }, 404

    return {
        'status': task.get('status', 'processing'),
        'progress': task.get('progress', 0),
        'message': task.get('message', 'Processing...'),
        # Batch runs report counts and the unmatched-track list here. Null for the
        # older create/merge jobs, which have nothing to report beyond a message.
        'result': task.get('result'),
    }
```

Add the two new routes after `create_playlist_from_file`:

```python
@app.route('/api/station-playlists')
def api_station_playlists():
    """The configured station -> Spotify playlist name map, for the batch form"""
    return {'status': 'success', 'stations': station_playlists.get_configured_stations()}


@app.route('/create-playlist-batch', methods=['POST'])
def create_playlist_batch():
    """Upsert a station's stored playlists over a date range into its Spotify playlist"""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {
                'status': 'error',
                'message': 'A JSON body with station_id, start_date and end_date is required'
            }, 400

        station_id = str(data.get('station_id') or '').strip()
        start_raw = str(data.get('start_date') or '').strip()
        end_raw = str(data.get('end_date') or '').strip()

        if not (station_id and start_raw and end_raw):
            return {
                'status': 'error',
                'message': 'station_id, start_date and end_date are all required'
            }, 400

        if not station_playlists.get_playlist_name(station_id):
            return {
                'status': 'error',
                'message': f'Station {station_id} is not configured in station_playlists.json'
            }, 400

        try:
            start_date = playlist_batch.parse_iso_date(start_raw)
            end_date = playlist_batch.parse_iso_date(end_raw)
        except ValueError:
            return {
                'status': 'error',
                'message': 'start_date and end_date must be dates in YYYY-MM-DD format'
            }, 400

        if start_date > end_date:
            return {
                'status': 'error',
                'message': 'start_date must not be after end_date'
            }, 400

        # Copy the session here, in the request context. Reading it inside the thread
        # raises "Working outside of request context" once the response has been sent.
        session_data = dict(session)
        if not spotify_playlist.has_cached_token(session_data):
            return SPOTIFY_AUTH_REQUIRED, 401

        task_id = str(uuid.uuid4())

        def run_batch():
            try:
                playlist_batch.run_range_batch(
                    station_id, start_date, end_date, task_id, session_data
                )
            except Exception as e:
                logging.error(f"Error in background playlist batch: {e}")
                spotify_playlist.update_task(
                    task_id, status='error', message=f'Error during batch: {str(e)}'
                )

        thread = threading.Thread(target=run_batch)
        thread.daemon = True
        thread.start()

        return {
            'status': 'success',
            'task_id': task_id,
            'message': f'Started batch for station {station_id}'
        }

    except Exception as e:
        logging.error(f"Error starting playlist batch: {e}")
        return {
            'status': 'error',
            'message': f'Error starting batch: {str(e)}'
        }, 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_batch_route.py tests/test_scheduled_job_pushes_to_spotify.py -v`
Expected: 20 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_batch_route.py tests/test_scheduled_job_pushes_to_spotify.py
git commit -m "Expose the batch endpoint and run the push after the nightly scrape"
```

---

### Task 11: Batch form on the playlists page

**Files:**
- Create: `static/ts/components/BatchPlaylistForm.tsx`
- Modify: `static/ts/types.ts`
- Modify: `static/ts/components/styles.ts`
- Modify: `static/ts/components/PlaylistsPage.tsx`

**Interfaces:**
- Consumes: `GET /api/station-playlists`, `POST /create-playlist-batch`, `GET /playlist_progress/<task_id>` including its `result` field (Task 10)
- Produces: the `BatchPlaylistForm` component, and `BatchResult` / an optional `result` on `PlaylistProgress` in `types.ts`

- [ ] **Step 1: Add the types**

In `static/ts/types.ts`, replace the `PlaylistProgress` interface and add `BatchResult`:

```typescript
export interface BatchResult {
  playlist_id?: string;
  playlist_name: string;
  added: number;
  skipped_existing: number;
  unmatched: Array<{ artist: string; song: string }>;
  files: number;
}

export interface PlaylistProgress {
  status: 'processing' | 'completed' | 'error';
  progress: number;
  message: string;
  // Only batch runs report one; the create/merge jobs leave it null.
  result?: BatchResult | null;
}
```

- [ ] **Step 2: Add the styles**

Append to `static/ts/components/styles.ts`:

```typescript
export const BatchForm = styled.form`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  padding: 16px;
  margin-bottom: 20px;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fafafa;
`;

export const BatchField = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: #555;
`;

export const BatchSelect = styled.select`
  padding: 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 14px;
  min-width: 180px;
`;

export const BatchDateInput = styled.input`
  padding: 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 14px;
`;

export const UnmatchedList = styled.ul`
  margin: 8px 0 0;
  padding-left: 20px;
  max-height: 200px;
  overflow-y: auto;
  font-size: 13px;
  color: #666;
`;
```

- [ ] **Step 3: Write the component**

Create `static/ts/components/BatchPlaylistForm.tsx`:

```typescript
import React, { useEffect, useRef, useState } from 'react';
import { BatchResult, PlaylistProgress } from '../types';
import { ProgressBar } from './ProgressBar';
import {
  BatchForm,
  BatchField,
  BatchSelect,
  BatchDateInput,
  AddButton,
  ConnectSpotifyLink,
  StatusMessage,
  UnmatchedList,
} from './styles';

const today = () => new Date().toISOString().slice(0, 10);

export const BatchPlaylistForm: React.FC = () => {
  const [stations, setStations] = useState<Record<string, string>>({});
  const [stationId, setStationId] = useState<string>('');
  const [startDate, setStartDate] = useState<string>(today());
  const [endDate, setEndDate] = useState<string>(today());
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set from the `auth_url` the server returns, so the control that failed is also
  // where the user can fix it - same pattern as PlaylistItem.
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [result, setResult] = useState<BatchResult | null>(null);
  const [progress, setProgress] = useState<PlaylistProgress>({
    status: 'processing',
    progress: 0,
    message: 'Initializing...',
  });
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    fetch('/api/station-playlists')
      .then((response) => response.json())
      .then((data) => {
        if (data.status === 'success') {
          setStations(data.stations);
          const ids = Object.keys(data.stations);
          if (ids.length > 0) {
            setStationId(ids[0]);
          }
        }
      })
      .catch((e) => console.error('Error fetching station playlists:', e));

    // Clear the interval on unmount, or it keeps polling a task nobody is watching.
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
      }
    };
  }, []);

  const pollProgress = (taskId: string) => {
    pollRef.current = window.setInterval(async () => {
      try {
        const response = await fetch(`/playlist_progress/${taskId}`);
        const data = await response.json();

        setProgress({
          status: data.status,
          progress: data.progress,
          message: data.message,
        });

        if (data.status === 'completed' || data.status === 'error') {
          if (pollRef.current !== null) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
          }
          setIsRunning(false);
          setResult(data.result ?? null);
        }
      } catch (e) {
        if (pollRef.current !== null) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setIsRunning(false);
        setProgress({
          status: 'error',
          progress: 0,
          message: 'Lost contact with the server while running the batch',
        });
      }
    }, 1000);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setAuthUrl(null);
    setResult(null);
    setIsRunning(true);
    setProgress({ status: 'processing', progress: 0, message: 'Starting batch...' });

    try {
      const response = await fetch('/create-playlist-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          station_id: stationId,
          start_date: startDate,
          end_date: endDate,
        }),
      });
      const data = await response.json();

      if (data.status === 'success') {
        pollProgress(data.task_id);
      } else {
        setIsRunning(false);
        setError(data.message || 'Could not start the batch');
        setAuthUrl(data.auth_url || null);
      }
    } catch (e) {
      setIsRunning(false);
      setError('Network error while starting the batch');
    }
  };

  const stationIds = Object.keys(stations);

  if (stationIds.length === 0) {
    return null;
  }

  return (
    <div>
      <BatchForm onSubmit={handleSubmit}>
        <BatchField>
          Station
          <BatchSelect
            value={stationId}
            onChange={(e) => setStationId(e.target.value)}
            disabled={isRunning}
          >
            {stationIds.map((id) => (
              <option key={id} value={id}>
                {stations[id]} ({id})
              </option>
            ))}
          </BatchSelect>
        </BatchField>
        <BatchField>
          From
          <BatchDateInput
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            disabled={isRunning}
          />
        </BatchField>
        <BatchField>
          To
          <BatchDateInput
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            disabled={isRunning}
          />
        </BatchField>
        <AddButton type="submit" disabled={isRunning}>
          {isRunning ? 'Running...' : 'Add to Spotify playlist'}
        </AddButton>
      </BatchForm>

      <ProgressBar active={isRunning || progress.status !== 'processing'} progress={progress} />

      {error && (
        <StatusMessage type="error">
          {error}
          {authUrl && <ConnectSpotifyLink href={authUrl}>Connect Spotify</ConnectSpotifyLink>}
        </StatusMessage>
      )}

      {result && (
        <StatusMessage type="success">
          Added {result.added} track{result.added === 1 ? '' : 's'} to "{result.playlist_name}"
          from {result.files} file{result.files === 1 ? '' : 's'} —{' '}
          {result.skipped_existing} already present, {result.unmatched.length} not found on Spotify.
          {result.unmatched.length > 0 && (
            <UnmatchedList>
              {result.unmatched.map((track, index) => (
                <li key={`${track.artist}-${track.song}-${index}`}>
                  {track.artist} — {track.song}
                </li>
              ))}
            </UnmatchedList>
          )}
        </StatusMessage>
      )}
    </div>
  );
};
```

- [ ] **Step 4: Mount it on the playlists page**

In `static/ts/components/PlaylistsPage.tsx`, add the import:

```typescript
import { BatchPlaylistForm } from './BatchPlaylistForm';
```

and render it directly under the heading, replacing this line:

```typescript
      <h1>Radio Playlists</h1>
```

with:

```typescript
      <h1>Radio Playlists</h1>
      <BatchPlaylistForm />
```

- [ ] **Step 5: Type-check**

Run: `cd static && npx tsc --noEmit`
Expected: no output (no errors)

- [ ] **Step 6: Build the bundle**

Run: `cd static && npm run build`
Expected: writes `static/dist/main.js` with no errors

- [ ] **Step 7: Commit**

```bash
git add static/ts/types.ts static/ts/components/styles.ts static/ts/components/BatchPlaylistForm.tsx static/ts/components/PlaylistsPage.tsx
git commit -m "Add the batch form to the playlists page"
```

---

### Task 12: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.env.template`

- [ ] **Step 1: Document the new environment variables**

Append to `.env.template`:

```
# Optional. Path to the station -> Spotify playlist name map.
# Defaults to station_playlists.json beside the application modules.
STATION_PLAYLISTS_CONFIG=

# Optional. Where the Spotify refresh token used by the nightly batch is stored.
# Defaults to spotify_token.json inside PLAYLIST_DATA_DIR. This file is a long-lived
# credential for the connected Spotify account - keep it off shared storage.
SPOTIFY_TOKEN_STORE=
```

- [ ] **Step 2: Document the feature in CLAUDE.md**

In `CLAUDE.md`, add this section immediately after the "Async work and progress" section:

```markdown
### Batch upsert into a configured playlist

`station_playlists.json` maps a station id to the Spotify playlist its tracks belong
to (`{"16134": "Radio FM"}`); `STATION_PLAYLISTS_CONFIG` overrides the path. **Station
ids are opaque strings** - Radoxo ids look numeric but the radiotut source uses the
slug `retrofm`, and both write into the same bucket. The file is re-read on every
call, so editing it needs no restart. A broken config yields an empty mapping and a
400 from the endpoint, never an import-time crash.

`POST /create-playlist-batch` (`{station_id, start_date, end_date}`, YYYY-MM-DD, both
ends inclusive) selects stored CSVs **by the load date in the filename**, not by the
play times inside them, then upserts into the configured playlist: deduped within the
batch and against the playlist's existing track URIs, so running it twice adds nothing
the second time. It returns a `task_id` polled at `/playlist_progress`, whose response
now carries a nullable `result` with `added`, `skipped_existing`, `files` and the full
list of tracks Spotify could not find.

`playlist_batch.process_new_playlists()` runs from `my_scheduled_job` right after the
scrape and does the same for files that have not been pushed yet. `processed_playlists.json`
in the bucket records which those are. **A key is recorded only once its upsert
succeeded** - a Spotify outage leaves those files for tomorrow rather than swallowing
them. `load_processed_keys` returns `None` (not an empty set) when the marker cannot be
read, and the job bootstraps an empty marker rather than treating an S3 failure as "nothing
processed yet"; if that write also fails it skips the run instead of reprocessing the
whole bucket.

The nightly job has no Flask session, so it authenticates from `spotify_token_store` -
`spotify_token.json` in `DATA_DIR`, written owner-only (0600) via a temp file plus
atomic rename, overridable with `SPOTIFY_TOKEN_STORE`. It is written on every OAuth
callback and **deleted by `/spotify/logout`**. It is a long-lived credential granting
playlist read/write, which is why it lives on the volume (already gitignored) rather
than in the S3 bucket next to the CSVs. `create_spotify_client_from_store` builds the
client over a `WriteThroughTokenStore`, a dict that re-persists the file when spotipy
refreshes the token - without it a mid-run refresh would be lost exactly the way a
`dict(session)` copy loses one.
```

- [ ] **Step 3: Verify the full suite and the build one last time**

Run: `uv run pytest`
Expected: all pass

Run: `cd static && npx tsc --noEmit`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .env.template
git commit -m "Document the batch upsert, its config and its token store"
```

---

## Manual verification

After Task 12, before opening a PR:

1. Put real playlist names in `station_playlists.json` for the ids in
   `load_playlist.RADOXO_STATION_IDS` plus `retrofm`.
2. `PLAYLIST_DATA_DIR=./data BASIC_AUTH_DISABLED=true uv run flask run --debug -h 0.0.0.0 -p 8001`
3. Open `http://localhost:8001/`, connect Spotify, and confirm `data/spotify_token.json`
   exists with mode `600` (`ls -l data/spotify_token.json`).
4. Run a one-day batch for a station that has files in the bucket; watch the progress
   bar and check the resulting playlist in Spotify.
5. Run the identical batch again — it must report `added: 0` and everything skipped.
6. Log out and confirm `data/spotify_token.json` is gone.
