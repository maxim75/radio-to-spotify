"""
Outcome accounting for scrape_and_upload_playlists.

Both the cron job and /load_playlist report what happened purely from the
(uploaded, failures) pair this returns. If a playlist that never reached S3 is
counted as uploaded, the failure is invisible in the log and in the API response -
which is the same class of silent breakage that filled the bucket with 877
one-byte CSVs.
"""

import os

import pandas as pd
import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app
import load_playlist
import playlist_upload


TRACKS = pd.DataFrame(
    [{"time": "2025-10-07T10:00:00", "artist_name": "A", "song_name": "S"}],
    columns=load_playlist.PLAYLIST_COLUMNS,
)


@pytest.fixture
def sources(monkeypatch, tmp_path):
    """
    Point both scrapers at fixed data and capture what gets handed to S3.

    Returns a dict the test mutates to decide whether the upload succeeds.
    """
    monkeypatch.setattr(load_playlist, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(flask_app.load_playlist, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(load_playlist, "RADOXO_STATION_IDS", [])

    state = {"upload_succeeds": True, "uploaded_files": []}

    def fake_scrape_retrofm():
        path = tmp_path / "playlist_retrofm_20251007_120000.csv"
        TRACKS.to_csv(path, index=False, encoding="utf-8")
        return str(path)

    def fake_upload(file_name, bucket, object_name):
        state["uploaded_files"].append(object_name)
        return state["upload_succeeds"]

    monkeypatch.setattr(flask_app.load_playlist, "load_playlist", fake_scrape_retrofm)
    monkeypatch.setattr(flask_app.playlist_upload, "upload_file_to_s3", fake_upload)
    return state


def test_a_successful_upload_is_reported_as_uploaded(sources):
    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == ["playlist_retrofm_20251007_120000.csv"]
    assert failures == []


def test_a_failed_upload_is_reported_as_a_failure_not_a_success(sources):
    """The scrape worked, S3 did not. That is a failure, and it must be said so."""
    sources["upload_succeeds"] = False

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == []
    assert [source for source, _ in failures] == ["retrofm"]


def test_a_failed_radoxo_upload_is_reported_as_a_failure(monkeypatch, sources):
    monkeypatch.setattr(flask_app.load_playlist, "RADOXO_STATION_IDS", [38225])
    monkeypatch.setattr(
        flask_app.load_playlist, "get_playlist_from_radoxo", lambda sid, date: TRACKS
    )
    sources["upload_succeeds"] = False

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == []
    assert "38225" in [source for source, _ in failures]


def test_load_playlist_route_reports_a_failed_upload_as_an_error(sources):
    sources["upload_succeeds"] = False
    flask_app.app.config.update(TESTING=True)

    response = flask_app.app.test_client().get("/load_playlist")

    assert response.status_code == 500
    assert response.get_json()["status"] == "error"


def test_upload_reports_false_when_s3_rejects_the_put(monkeypatch, tmp_path):
    """
    playlist_upload swallows exceptions by design, so the return value is the only
    signal a caller gets that the object did not land.
    """
    source_file = tmp_path / "playlist.csv"
    source_file.write_text("time,artist_name,song_name\n", encoding="utf-8")

    class FailingClient:
        def upload_file(self, file_name, bucket, object_name):
            raise RuntimeError("AccessDenied")

    monkeypatch.setattr(playlist_upload.boto3, "client", lambda *a, **kw: FailingClient())

    assert playlist_upload.upload_file_to_s3(str(source_file), "b", "k") is False


def test_upload_reports_true_when_s3_accepts_the_put(monkeypatch, tmp_path):
    source_file = tmp_path / "playlist.csv"
    source_file.write_text("time,artist_name,song_name\n", encoding="utf-8")

    class WorkingClient:
        def upload_file(self, file_name, bucket, object_name):
            return None

    monkeypatch.setattr(playlist_upload.boto3, "client", lambda *a, **kw: WorkingClient())

    assert playlist_upload.upload_file_to_s3(str(source_file), "b", "k") is True
