"""
The nightly job scrapes and then pushes, and neither half can silence the other.
"""

import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app


def test_the_push_runs_after_a_successful_scrape(monkeypatch):
    calls = []
    monkeypatch.setattr(flask_app, "scrape_and_upload_playlists",
                        lambda: calls.append("scrape") or (["a.csv"], []))
    monkeypatch.setattr(flask_app.playlist_batch, "process_new_playlists",
                        lambda: calls.append("push") or {"processed": ["a.csv"], "failures": []})

    flask_app.my_scheduled_job()

    assert calls == ["scrape", "push"]


def test_a_failing_push_does_not_hide_the_scrape_result(monkeypatch, caplog):
    monkeypatch.setattr(flask_app, "scrape_and_upload_playlists", lambda: (["a.csv"], []))

    def boom():
        raise RuntimeError("spotify exploded")

    monkeypatch.setattr(flask_app.playlist_batch, "process_new_playlists", boom)

    with caplog.at_level("INFO"):
        flask_app.my_scheduled_job()

    assert "uploaded 1 playlist(s)" in caplog.text
    assert "spotify exploded" in caplog.text


def test_a_failing_scrape_does_not_stop_the_push(monkeypatch):
    # Files uploaded on a previous night may still be unprocessed.
    calls = []

    def boom():
        raise RuntimeError("scrape exploded")

    monkeypatch.setattr(flask_app, "scrape_and_upload_playlists", boom)
    monkeypatch.setattr(flask_app.playlist_batch, "process_new_playlists",
                        lambda: calls.append("push") or {"processed": [], "failures": []})

    flask_app.my_scheduled_job()

    assert calls == ["push"]
