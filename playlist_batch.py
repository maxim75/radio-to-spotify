"""
Turning stored playlist CSVs into Spotify playlist updates.

Filenames look like playlist_16134_20260814_072254.csv: station id, the date the
playlist was loaded, and the time it was loaded. The date range a caller supplies
selects on that load date - not on the play times inside the file - so selection
is a cheap filter over the bucket's key list rather than a download of everything.
"""

import datetime
import json
import logging
import re
import threading
from io import StringIO

import pandas as pd

from playlist_upload import download_file_from_s3, put_object_to_s3

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
