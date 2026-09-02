"""
Persistence of the Spotify refresh token used by the unattended nightly job.

This file is a long-lived credential granting playlist read/write on the user's
Spotify account. Two properties matter and are asserted here: it is never
world-readable, and a token spotipy refreshes mid-job is written back rather
than lost (the same class of bug as passing a dict(session) copy to a thread).
"""

import json
import os

import pytest

import spotify_token_store


TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at": 1234567890}


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "spotify_token.json")
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", path)
    return path


def test_a_saved_token_round_trips(store):
    assert spotify_token_store.save_token(TOKEN) is True

    assert spotify_token_store.load_token() == TOKEN


def test_the_token_file_is_owner_only(store):
    spotify_token_store.save_token(TOKEN)

    assert oct(os.stat(store).st_mode & 0o777) == "0o600"


def test_loading_an_absent_store_returns_none(store):
    assert spotify_token_store.load_token() is None


def test_a_token_without_a_refresh_token_is_rejected(store):
    # Without a refresh token the unattended job cannot outlive the access token's
    # hour, so treat it as no stored credential at all rather than a working one.
    with open(store, "w", encoding="utf-8") as handle:
        json.dump({"access_token": "a"}, handle)

    assert spotify_token_store.load_token() is None


def test_malformed_json_is_rejected(store):
    with open(store, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    assert spotify_token_store.load_token() is None


def test_saving_replaces_an_existing_token(store):
    spotify_token_store.save_token(TOKEN)
    spotify_token_store.save_token({**TOKEN, "access_token": "b"})

    assert spotify_token_store.load_token()["access_token"] == "b"


def test_saving_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "spotify_token.json"))

    spotify_token_store.save_token(TOKEN)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["spotify_token.json"]


def test_saving_cleans_up_temp_file_on_replace_failure(tmp_path, monkeypatch):
    # Verify the cleanup code by injecting a failure between mkstemp and os.replace.
    # A successful save trivially leaves no temp file, so we must test the error path.
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "spotify_token.json"))

    def raise_error(*args, **kwargs):
        raise IOError("mock failure at replace")

    monkeypatch.setattr(spotify_token_store.os, "replace", raise_error)

    assert spotify_token_store.save_token(TOKEN) is False

    # Verify no temp files were left behind
    temp_files = [p.name for p in tmp_path.iterdir() if ".spotify_token." in p.name and p.name.endswith(".tmp")]
    assert temp_files == []


def test_clear_token_removes_the_file(store):
    spotify_token_store.save_token(TOKEN)

    assert spotify_token_store.clear_token() is True
    assert not os.path.exists(store)


def test_clear_token_on_an_absent_store_reports_nothing_removed(store):
    assert spotify_token_store.clear_token() is False


def test_save_reports_failure_rather_than_raising(tmp_path, monkeypatch):
    # A store failure must never break interactive login, so it is signalled by the
    # return value like everything else in this codebase.
    monkeypatch.setenv("SPOTIFY_TOKEN_STORE", str(tmp_path / "no" / "such" / "dir" / "t.json"))
    monkeypatch.setattr(spotify_token_store.os, "makedirs", lambda *a, **k: None)

    assert spotify_token_store.save_token(TOKEN) is False


def test_the_write_through_store_persists_a_refreshed_token(store):
    session_data = spotify_token_store.WriteThroughTokenStore({"spotify_token_info": TOKEN})

    refreshed = {**TOKEN, "access_token": "refreshed"}
    session_data["spotify_token_info"] = refreshed

    assert spotify_token_store.load_token() == refreshed


def test_the_write_through_store_ignores_unrelated_keys(store):
    session_data = spotify_token_store.WriteThroughTokenStore()

    session_data["something_else"] = "value"

    assert spotify_token_store.load_token() is None


def test_constructing_the_write_through_store_does_not_write(store):
    spotify_token_store.WriteThroughTokenStore({"spotify_token_info": TOKEN})

    assert not os.path.exists(store)
