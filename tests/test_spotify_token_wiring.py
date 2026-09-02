"""
The stored token is armed by logging in and disarmed by logging out.

Leaving an unattended credential live after an explicit logout is the wrong
surprise, and an unattended client built with no token is worse than none: spotipy
falls back to its interactive console flow and dies on EOF inside the scheduler.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import spotify_playlist
import spotify_token_store


TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at": 9999999999}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "spotify_token.json"))
    return str(tmp_path / "spotify_token.json")


class FakeAuthManager:
    def __init__(self, cache_handler):
        self.cache_handler = cache_handler

    def get_access_token(self, code, check_cache=False):
        return TOKEN


def test_a_successful_login_arms_the_store(store, monkeypatch):
    session = {}
    monkeypatch.setattr(
        spotify_playlist, "create_spotify_auth_manager",
        lambda session_data=None: FakeAuthManager(spotify_playlist.SessionCacheHandler(session_data)),
    )

    assert spotify_playlist.handle_oauth_callback("code", session) is True
    assert session["spotify_token_info"] == TOKEN
    assert spotify_token_store.load_token() == TOKEN


def test_a_store_failure_does_not_break_login(store, monkeypatch):
    session = {}
    monkeypatch.setattr(
        spotify_playlist, "create_spotify_auth_manager",
        lambda session_data=None: FakeAuthManager(spotify_playlist.SessionCacheHandler(session_data)),
    )
    monkeypatch.setattr(spotify_token_store, "save_token", lambda token_info: False)

    assert spotify_playlist.handle_oauth_callback("code", session) is True
    assert session["spotify_token_info"] == TOKEN


def test_logout_clears_the_store(store):
    spotify_token_store.save_token(TOKEN)
    session = {"spotify_token_info": TOKEN}

    assert spotify_playlist.clear_spotify_token(session) is True
    assert "spotify_token_info" not in session
    assert spotify_token_store.load_token() is None


def test_logout_clears_the_store_even_with_no_session_token(store):
    spotify_token_store.save_token(TOKEN)

    spotify_playlist.clear_spotify_token({})

    assert spotify_token_store.load_token() is None


def test_no_stored_token_means_no_unattended_client(store):
    assert spotify_playlist.create_spotify_client_from_store() is None


def test_an_unattended_client_is_built_over_a_write_through_store(store, monkeypatch):
    spotify_token_store.save_token(TOKEN)
    captured = {}

    def fake_create(session_data):
        captured["session_data"] = session_data
        return "spotify-client"

    monkeypatch.setattr(spotify_playlist, "create_spotify_client_with_session", fake_create)

    assert spotify_playlist.create_spotify_client_from_store() == "spotify-client"
    assert isinstance(captured["session_data"], spotify_token_store.WriteThroughTokenStore)
    assert captured["session_data"]["spotify_token_info"] == TOKEN
