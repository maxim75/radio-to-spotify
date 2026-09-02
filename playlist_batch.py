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
