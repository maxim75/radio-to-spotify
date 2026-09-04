"""
Which Spotify playlist each radio station's tracks belong to, and which scraper
feeds it.

Station ids are opaque strings, not numbers: Radoxo ids look like "16134" but the
radiotut source uses the slug "retrofm", and both write files into the same bucket
with the same filename shape.

This file is the single source of truth for the nightly run - both what gets scraped
and where the tracks land - and it is edited through /stations in the UI as well as
by hand, so the writer keeps it indented and readable.

Every failure here degrades to an empty (or partial) mapping rather than raising.
A bad config file must not stop the app from booting - the batch endpoint answers
400 "station not configured", which is a far more useful thing for a caller to read
than a 500 from an import-time crash.
"""

import json
import logging
import os
import shutil
import tempfile
import threading

# The scrapers a station can be fed by; the nightly job dispatches on this value, so
# an entry naming anything else has no scraper behind it and is dropped on load.
SOURCES = ("radoxo", "radiotut")

# The copy that ships in the image (Dockerfile COPYs it next to the modules). It is
# the seed for the configured path, never the thing the UI writes to when they differ.
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "station_playlists.json"
)

# Overridable so a container can keep its config on the mounted volume, where edits
# survive a rebuild, without bind-mounting a file over the image's copy.
# `or`, not os.environ.get(NAME, default): a *present but empty* var (as .env.template
# used to ship it, uncommented with no value) must still fall through to the default
# rather than becoming CONFIG_PATH = "" and making every open() raise FileNotFoundError.
CONFIG_PATH = os.environ.get("STATION_PLAYLISTS_CONFIG") or DEFAULT_CONFIG_PATH

# Held across the read-modify-write the /api/stations routes do. uWSGI runs one
# process with many threads (Dockerfile -p 1 --threads 8), so a plain lock is enough
# to stop two concurrent edits from losing one of them.
config_lock = threading.Lock()


def _default_source(station_id):
    """
    The source for a record that does not name one.

    Files written before the source field existed hold a bare playlist name. The only
    non-numeric id in that world was the radiotut slug retrofm, so this reproduces the
    previous behaviour exactly rather than guessing.
    """
    return "radoxo" if station_id.isdigit() else "radiotut"


def _read_raw(path):
    """The parsed JSON object at `path`, or None when it cannot be used."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        logging.error(
            "Station playlist config not found at %s - no station is configured, so "
            "nothing will be scraped and /create-playlist-batch will reject every "
            "request.", path
        )
        return None
    except (json.JSONDecodeError, OSError) as e:
        logging.error("Could not read station playlist config %s: %s", path, e)
        return None

    if not isinstance(raw, dict):
        logging.error(
            "Station playlist config %s must be a JSON object mapping station id to "
            "its settings, got %s", path, type(raw).__name__
        )
        return None

    return raw


def load_stations(path=None):
    """
    The configured stations as {station_id, playlist_name, source} records, in file
    order. Returns [] if the file cannot be read.

    Both shapes are accepted, so a config written before the source field existed
    still loads:

        {"retrofm": "Retro FM"}
        {"retrofm": {"playlist_name": "Retro FM", "source": "radiotut"}}
    """
    # Only None means "not passed" - an explicitly-passed "" must be attempted (and
    # fail loudly) rather than silently falling back to CONFIG_PATH.
    path = CONFIG_PATH if path is None else path

    raw = _read_raw(path)
    if raw is None:
        return []

    stations = []
    for station_id, value in raw.items():
        station_id = str(station_id)

        if isinstance(value, str):
            playlist_name, source = value, _default_source(station_id)
        elif isinstance(value, dict):
            playlist_name = value.get("playlist_name")
            source = value.get("source") or _default_source(station_id)
        else:
            logging.warning(
                "Skipping station %r in %s: expected a playlist name or an object, got %s",
                station_id, path, type(value).__name__
            )
            continue

        if not isinstance(playlist_name, str) or not playlist_name.strip():
            logging.warning(
                "Skipping station %r in %s: the playlist name must be a non-empty string",
                station_id, path
            )
            continue

        if source not in SOURCES:
            logging.warning(
                "Skipping station %r in %s: %r is not one of the sources this app can "
                "scrape (%s)", station_id, path, source, ", ".join(SOURCES)
            )
            continue

        stations.append({
            "station_id": station_id,
            "playlist_name": playlist_name.strip(),
            "source": source,
        })

    return stations


def save_stations(stations, path=None):
    """
    Write the records back atomically. Returns True when the file landed.

    Like playlist_upload and spotify_token_store, this swallows its exception and
    signals through the return value; the caller turns False into a 500.
    """
    path = CONFIG_PATH if path is None else path
    directory = os.path.dirname(path) or "."
    payload = {
        station["station_id"]: {
            "playlist_name": station["playlist_name"],
            "source": station["source"],
        }
        for station in stations
    }
    handle = None
    tmp_path = None

    try:
        os.makedirs(directory, exist_ok=True)
        # Write beside the target and rename, so a crash mid-write cannot leave a
        # truncated config that would silently configure no stations at all.
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".station_playlists.", suffix=".tmp")
        handle = os.fdopen(fd, "w", encoding="utf-8")
        # Indented and not \u-escaped: unlike the token store this write path copies,
        # this file is also read and edited by a human, and the names are Cyrillic.
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.close()
        handle = None
        # mkstemp creates 0600, which is right for a credential and wrong for a config
        # anyone on the box may read.
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
        logging.info("Wrote %d station(s) to %s", len(payload), path)
        return True
    except Exception as e:
        logging.error("Could not write the station playlist config %s: %s", path, e)
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


def bootstrap_config():
    """
    Seed CONFIG_PATH from the copy in the image when it does not exist yet.

    STATION_PLAYLISTS_CONFIG points at the mounted volume in production, which starts
    out empty. Without this, the first boot after that change reads no stations - and
    an empty config now means the nightly job scrapes nothing, not merely that it
    pushes nothing to Spotify.
    """
    if CONFIG_PATH == DEFAULT_CONFIG_PATH or os.path.exists(CONFIG_PATH):
        return False

    if not os.path.exists(DEFAULT_CONFIG_PATH):
        logging.error(
            "No station config at %s and no default at %s to seed it from - no station "
            "is configured.", CONFIG_PATH, DEFAULT_CONFIG_PATH
        )
        return False

    try:
        os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)
        logging.info("Seeded the station config at %s from %s", CONFIG_PATH, DEFAULT_CONFIG_PATH)
        return True
    except OSError as e:
        logging.error("Could not seed the station config at %s: %s", CONFIG_PATH, e)
        return False


def load_config(path=None):
    """The station -> playlist-name map. Returns {} if the config cannot be read."""
    return {s["station_id"]: s["playlist_name"] for s in load_stations(path)}


def get_configured_stations():
    """The full station -> playlist-name map from the default config path."""
    return load_config()


def get_playlist_name(station_id):
    """The configured playlist name for a station, or None if it is not configured."""
    return load_config().get(str(station_id))
