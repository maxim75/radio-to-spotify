"""
Outcome accounting for scrape_and_upload_playlists.

Both the cron job and /load_playlist report what happened purely from the
(uploaded, failures) pair this returns. If a playlist that never reached S3 is
counted as uploaded, the failure is invisible in the log and in the API response -
which is the same class of silent breakage that filled the bucket with 877
one-byte CSVs.

The station list comes from the config file rather than a Python literal, so these
tests drive it by monkeypatching load_stations.
"""

import os

import pandas as pd
import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app
import load_playlist
import playlist_upload
import station_playlists


TRACKS = pd.DataFrame(
    [{"time": "2025-10-07T10:00:00", "artist_name": "A", "song_name": "S"}],
    columns=load_playlist.PLAYLIST_COLUMNS,
)

RETROFM = {"station_id": "retrofm", "playlist_name": "Retro FM", "source": "radiotut"}
RADOXO = {"station_id": "38225", "playlist_name": "Radio 38225", "source": "radoxo"}


@pytest.fixture
def sources(monkeypatch, tmp_path):
    """
    Point both scrapers at fixed data and capture what gets handed to S3.

    Returns a dict the test mutates to decide which stations are configured and
    whether the upload succeeds.
    """
    monkeypatch.setattr(load_playlist, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(flask_app.load_playlist, "DATA_DIR", str(tmp_path))

    state = {"upload_succeeds": True, "uploaded_files": [], "stations": [RETROFM], "scraped": []}

    def fake_radiotut(station_id, day):
        state["scraped"].append(("radiotut", str(station_id)))
        return TRACKS

    def fake_radoxo(station_id, date):
        state["scraped"].append(("radoxo", str(station_id)))
        return TRACKS

    def fake_upload(file_name, bucket, object_name):
        state["uploaded_files"].append(object_name)
        return state["upload_succeeds"]

    monkeypatch.setattr(station_playlists, "load_stations", lambda *a, **kw: state["stations"])
    monkeypatch.setattr(flask_app.load_playlist, "get_playlist_from_radiotut", fake_radiotut)
    monkeypatch.setattr(flask_app.load_playlist, "get_playlist_from_radoxo", fake_radoxo)
    monkeypatch.setattr(flask_app.playlist_upload, "upload_file_to_s3", fake_upload)
    return state


def test_a_successful_upload_is_reported_as_uploaded(sources):
    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert failures == []
    assert len(uploaded) == 1
    assert uploaded[0].startswith("playlist_retrofm_")
    assert uploaded[0].endswith(".csv")


def test_a_failed_upload_is_reported_as_a_failure_not_a_success(sources):
    """The scrape worked, S3 did not. That is a failure, and it must be said so."""
    sources["upload_succeeds"] = False

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == []
    assert [source for source, _ in failures] == ["retrofm"]


def test_a_failed_radoxo_upload_is_reported_as_a_failure(sources):
    sources["stations"] = [RADOXO]
    sources["upload_succeeds"] = False

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == []
    assert "38225" in [source for source, _ in failures]


def test_each_station_is_scraped_from_the_source_it_is_configured_with(sources):
    sources["stations"] = [RETROFM, RADOXO]

    flask_app.scrape_and_upload_playlists()

    assert sources["scraped"] == [("radiotut", "retrofm"), ("radoxo", "38225")]


def test_one_failing_station_does_not_stop_the_others(sources, monkeypatch):
    sources["stations"] = [RETROFM, RADOXO]
    monkeypatch.setattr(
        flask_app.load_playlist,
        "get_playlist_from_radiotut",
        lambda sid, day: (_ for _ in ()).throw(RuntimeError("page layout changed")),
    )

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert [source for source, _ in failures] == ["retrofm"]
    assert len(uploaded) == 1


def test_an_empty_scrape_is_a_failure_and_is_never_uploaded(sources, monkeypatch):
    # Writing an empty frame is what produced the 877 one-byte CSVs in the bucket.
    empty = pd.DataFrame([], columns=load_playlist.PLAYLIST_COLUMNS)
    monkeypatch.setattr(flask_app.load_playlist, "get_playlist_from_radiotut", lambda sid, day: empty)

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == []
    assert sources["uploaded_files"] == []
    assert [source for source, _ in failures] == ["retrofm"]


def test_an_empty_configuration_is_reported_rather_than_looking_like_a_clean_run(sources):
    # An unreadable or empty config now means nothing is scraped at all, so it has to
    # be louder than an empty success.
    sources["stations"] = []

    uploaded, failures = flask_app.scrape_and_upload_playlists()

    assert uploaded == []
    assert [source for source, _ in failures] == ["config"]


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
