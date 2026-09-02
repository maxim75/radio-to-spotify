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
