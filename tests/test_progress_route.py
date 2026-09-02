"""
The /playlist_progress/<task_id> route.

This is the endpoint that broke under multiple uWSGI processes: the POST that
started a job and the GET that polled it ran in different address spaces, so the
poll could not see the task. These tests pin the contract the browser depends on.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app
import spotify_playlist


@pytest.fixture
def client():
    flask_app.app.config.update(TESTING=True)
    spotify_playlist.reset_tasks()
    yield flask_app.app.test_client()
    spotify_playlist.reset_tasks()


def test_poll_reports_progress_for_a_task_started_elsewhere(client):
    spotify_playlist.start_task("task-1")
    spotify_playlist.update_task("task-1", progress=45, message="Searching...")

    response = client.get("/playlist_progress/task-1")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "processing",
        "progress": 45,
        "message": "Searching...",
        "result": None,
    }


def test_poll_for_an_unknown_task_is_a_404(client):
    response = client.get("/playlist_progress/never-created")

    assert response.status_code == 404
    assert response.get_json()["status"] == "error"


def test_poll_response_carries_exactly_the_fields_the_client_reads(client):
    """PlaylistItem.tsx and SpotifyPlaylistsPage.tsx read status/progress/message/result."""
    spotify_playlist.start_task("task-1")

    body = client.get("/playlist_progress/task-1").get_json()

    assert set(body) == {"status", "progress", "message", "result"}
