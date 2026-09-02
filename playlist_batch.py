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

from playlist_upload import download_file_from_s3, list_objects_in_bucket, put_object_to_s3

import spotify_playlist
import station_playlists

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
    (tracks, consumed_keys): unique (artist_name, song_name) pairs across `keys`, in
    first-seen order, and the subset of `keys` that were genuinely read.

    Deduping here rather than later matters: a station plays the same song many times
    a day, and every unique pair costs one Spotify search.

    consumed_keys deliberately distinguishes two failure shapes a caller must treat
    differently:

    - A failed download (download_file_from_s3 returned None) is a transient S3
      problem. Its key is *excluded* from consumed_keys so a caller does not mark it
      processed - it must be retried on a later run.
    - An empty or unparseable file - the bucket still holds one-byte CSVs from before
      the scrapers learned to raise on an empty scrape - will never yield a track no
      matter how many times it is read. Its key *is* included in consumed_keys, so a
      caller is correct to mark it processed forever.
    """
    seen = set()
    tracks = []
    consumed_keys = []

    for key in keys:
        content = download_file_from_s3(bucket, key)
        if content is None:
            logging.warning("Skipping %s: could not be downloaded", key)
            continue

        # Reaching here means the key was genuinely read, even if what came back is
        # empty or unparseable - both are permanent, not transient.
        consumed_keys.append(key)

        if not content.strip():
            logging.warning("Skipping %s: empty file", key)
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

    logging.info(
        "Collected %d unique track(s) from %d of %d file(s)",
        len(tracks), len(consumed_keys), len(keys)
    )
    return tracks, consumed_keys


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
        # This manual path never writes the processed marker (see save_processed_keys),
        # so it only needs the tracks - which keys were actually consumed matters to
        # the nightly job's bookkeeping, not to this one.
        tracks, _ = collect_tracks(bucket, keys)

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
                tracks, consumed_keys = collect_tracks(bucket, station_keys)
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
                # Only keys collect_tracks actually read - a key it could not download
                # is a transient S3 problem and must be retried on a later run, not
                # written off as processed alongside its station's other keys.
                newly_processed.extend(consumed_keys)
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
