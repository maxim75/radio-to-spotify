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
