"""
The nightly push of newly scraped files into Spotify.

The rule that carries the whole design: a key is recorded as processed only when
the upsert that consumed it succeeded. A Spotify outage must leave those files to
be retried tomorrow, not swallow them - which is the same failure mode as counting
a playlist as uploaded when S3 rejected it.
"""

import datetime
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
                        lambda bucket, keys: ([("Artist", "Song")], keys))
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


def test_a_stations_backlog_is_capped_per_run(nightly, monkeypatch):
    # The marker starts empty by design, so a first run after deploy could otherwise
    # see years of daily CSVs plus ~877 legacy one-byte files for a single station in
    # one go - one S3 GET and one Spotify search per unique track, all under
    # _marker_lock. The cap bounds every run and drains the backlog oldest-first over
    # successive nights instead.
    dates = [
        (datetime.date(2026, 1, 1) + datetime.timedelta(days=i)).strftime("%Y%m%d")
        for i in range(60)
    ]
    all_keys = [f"playlist_16134_{d}_000000.csv" for d in dates]
    # Shuffle the S3 listing order to prove the cap is applied after sorting, not
    # before - an unsorted "first 50" would not be the oldest 50.
    shuffled = list(reversed(all_keys))
    monkeypatch.setattr(playlist_batch, "list_objects_in_bucket", lambda bucket: shuffled)
    monkeypatch.setattr(playlist_batch.station_playlists, "get_configured_stations",
                        lambda: {"16134": "Radio FM"})

    outcome = playlist_batch.process_new_playlists()

    assert len(outcome["processed"]) == playlist_batch.MAX_KEYS_PER_STATION_PER_RUN
    assert sorted(outcome["processed"]) == all_keys[:playlist_batch.MAX_KEYS_PER_STATION_PER_RUN]

    # The remaining 10 keys were left untouched for a later run to pick up.
    nightly["processed"] = set(outcome["processed"])
    outcome_two = playlist_batch.process_new_playlists()
    assert sorted(outcome_two["processed"]) == all_keys[playlist_batch.MAX_KEYS_PER_STATION_PER_RUN:]


def test_an_empty_bucket_listing_is_a_reported_failure_not_a_clean_run(nightly, monkeypatch):
    # list_objects_in_bucket returns [] both on an S3 error and on a genuinely empty
    # bucket. The marker code in this module already treats None from
    # load_processed_keys as "could not read", not "nothing processed yet" - an empty
    # listing deserves the same suspicion rather than being logged as a clean
    # "0 file(s) processed, 0 failure(s)" run.
    monkeypatch.setattr(playlist_batch, "list_objects_in_bucket", lambda bucket: [])

    outcome = playlist_batch.process_new_playlists()

    assert outcome["processed"] == []
    assert nightly["upserts"] == []
    assert ("s3", "bucket listing returned nothing - S3 may be unreachable") in outcome["failures"]


def test_files_with_no_usable_tracks_are_still_marked_processed(nightly, monkeypatch):
    # Deliberate and destructive: a file that yields nothing never will, no matter
    # how many times it is re-read, so it must be marked processed rather than
    # re-downloaded every night forever. The fixture's default collect_tracks stub
    # always returns a track, so this pins the case where it does not - especially
    # now that a failed *download* (fix 3) behaves differently and must not be
    # confused with this one.
    monkeypatch.setattr(playlist_batch, "collect_tracks", lambda bucket, keys: ([], keys))

    outcome = playlist_batch.process_new_playlists()

    assert sorted(outcome["processed"]) == [
        "playlist_16134_20260814_072254.csv",
        "playlist_38225_20260814_072300.csv",
    ]
    assert outcome["failures"] == []
    assert nightly["upserts"] == []
