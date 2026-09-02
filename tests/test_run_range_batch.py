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
    monkeypatch.setattr(playlist_batch, "collect_tracks",
                        lambda bucket, keys: (state["tracks"], keys))
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


def test_an_empty_bucket_listing_does_not_claim_the_range_is_empty(batch, monkeypatch):
    # list_objects_in_bucket returns [] both when S3 is unreachable and when the
    # bucket genuinely has nothing - the task must still complete (an empty range is
    # a legitimate answer) but must not word its message as if it had positively
    # confirmed the range is empty.
    monkeypatch.setattr(playlist_batch, "list_objects_in_bucket", lambda bucket: [])

    assert playlist_batch.run_range_batch("16134", START, END, "t1", {}) is True

    assert batch["upsert_calls"] == []
    task = spotify_playlist.get_task("t1")
    assert task["status"] == "completed"
    assert task["result"]["files"] == 0
    assert "listing" in task["message"].lower()
    assert "no playlist files" not in task["message"].lower()
