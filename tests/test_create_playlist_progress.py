"""
Progress reporting for create_playlist_from_csv.

These exercise the real function against a stub Spotify client, so they cover the
path a user actually watches: the task entry the browser polls while the job runs
in its daemon thread.
"""

import pytest

import spotify_playlist


CSV = (
    "time,artist_name,song_name\n"
    "2025-10-07T10:00:00,Artist One,Song One\n"
    "2025-10-07T10:04:00,Artist Two,Song Two\n"
)


class StubSpotify:
    """Minimal stand-in for spotipy.Spotify covering only what the function calls."""

    def __init__(self, matchable=("Song One", "Song Two")):
        self.matchable = matchable
        self.added = []
        self.created_playlists = []

    def current_user(self):
        return {"id": "user-1"}

    def user_playlist_create(self, user_id, name, public=False):
        self.created_playlists.append(name)
        return {"id": "playlist-1"}

    def search(self, q, type="track", limit=1):
        matched = any(song.lower() in q.lower() for song in self.matchable)
        if not matched:
            return {"tracks": {"items": []}}
        return {"tracks": {"items": [{"uri": f"spotify:track:{len(self.added)}-{q}"}]}}

    def playlist_add_items(self, playlist_id, batch):
        self.added.extend(batch)


@pytest.fixture(autouse=True)
def clean_store():
    spotify_playlist.reset_tasks()
    yield
    spotify_playlist.reset_tasks()


@pytest.fixture
def stub_client(monkeypatch):
    client = StubSpotify()
    monkeypatch.setattr(
        spotify_playlist, "create_spotify_client_with_session", lambda session: client
    )
    return client


def test_successful_run_finishes_the_task_at_one_hundred_percent(stub_client):
    spotify_playlist.create_playlist_from_csv(CSV, "my-playlist", "task-1", {"tok": 1})

    task = spotify_playlist.get_task("task-1")
    assert task["status"] == "completed"
    assert task["progress"] == 100


def test_matched_tracks_are_added_to_the_new_playlist(stub_client):
    spotify_playlist.create_playlist_from_csv(CSV, "my-playlist", "task-1", {"tok": 1})

    assert stub_client.created_playlists == ["my-playlist"]
    assert len(stub_client.added) == 2


def test_task_reports_an_error_when_no_track_matches(monkeypatch):
    monkeypatch.setattr(
        spotify_playlist,
        "create_spotify_client_with_session",
        lambda session: StubSpotify(matchable=()),
    )

    spotify_playlist.create_playlist_from_csv(CSV, "my-playlist", "task-2", {"tok": 1})

    assert spotify_playlist.get_task("task-2")["status"] == "error"


def test_task_reports_an_error_when_the_session_holds_no_token(monkeypatch):
    monkeypatch.setattr(
        spotify_playlist, "create_spotify_client_with_session", lambda session: None
    )

    spotify_playlist.create_playlist_from_csv(CSV, "my-playlist", "task-3", {})

    task = spotify_playlist.get_task("task-3")
    assert task["status"] == "error"
    assert "authenticated" in task["message"].lower()
