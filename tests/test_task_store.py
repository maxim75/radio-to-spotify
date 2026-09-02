"""
Tests for the in-process task store backing playlist create/merge progress.

The store is deliberately in-memory: uWSGI runs a single process with threads, so
one address space holds every task, and a job's progress dies with the worker
that was running it anyway. What these tests pin down is that concurrent writers
cannot corrupt it and that it cannot grow without bound.
"""

import threading
import time

import pytest

import spotify_playlist


@pytest.fixture(autouse=True)
def clean_store():
    """Each test starts with an empty store, and leaves one behind."""
    spotify_playlist.reset_tasks()
    yield
    spotify_playlist.reset_tasks()


def test_started_task_reports_as_processing_at_zero():
    spotify_playlist.start_task("abc")

    task = spotify_playlist.get_task("abc")

    assert task["status"] == "processing"
    assert task["progress"] == 0


def test_start_task_accepts_an_initial_message():
    spotify_playlist.start_task("abc", message="Starting playlist merge...")

    assert spotify_playlist.get_task("abc")["message"] == "Starting playlist merge..."


def test_get_task_returns_none_for_unknown_id():
    assert spotify_playlist.get_task("never-created") is None


def test_update_task_merges_fields_into_existing_task():
    spotify_playlist.start_task("abc")

    spotify_playlist.update_task("abc", progress=42, message="Searching...")

    task = spotify_playlist.get_task("abc")
    assert task["progress"] == 42
    assert task["message"] == "Searching..."
    # Untouched fields survive a partial update.
    assert task["status"] == "processing"


def test_update_task_on_unknown_id_does_not_resurrect_it():
    """
    A job whose task was evicted must not silently recreate a half-populated
    entry: the poll endpoint would then report a task with no status at all.
    """
    spotify_playlist.update_task("never-created", progress=10)

    assert spotify_playlist.get_task("never-created") is None


def test_get_task_exposes_only_public_fields():
    """
    The TTL bookkeeping is the store's business. Callers serialise what they get,
    so an internal timestamp here ends up in an API response.
    """
    spotify_playlist.start_task("abc")

    assert set(spotify_playlist.get_task("abc")) == {"status", "progress", "message"}


def test_get_task_returns_a_copy_callers_cannot_mutate():
    spotify_playlist.start_task("abc")

    spotify_playlist.get_task("abc")["progress"] = 99

    assert spotify_playlist.get_task("abc")["progress"] == 0


def test_concurrent_updates_from_many_threads_all_land():
    """
    Every playlist job writes progress from its own daemon thread while the poll
    endpoint reads from a request thread. No update may be lost or raise.
    """
    for i in range(20):
        spotify_playlist.start_task(f"task-{i}")

    errors = []

    def drive(index):
        try:
            for progress in range(0, 101, 10):
                spotify_playlist.update_task(f"task-{index}", progress=progress)
                spotify_playlist.get_task(f"task-{index}")
        except Exception as exc:  # pragma: no cover - only on a real race
            errors.append(exc)

    threads = [threading.Thread(target=drive, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for i in range(20):
        assert spotify_playlist.get_task(f"task-{i}")["progress"] == 100


def test_tasks_older_than_the_ttl_are_evicted(monkeypatch):
    monkeypatch.setattr(spotify_playlist, "TASK_TTL_SECONDS", 0.05)
    spotify_playlist.start_task("stale")

    time.sleep(0.1)
    spotify_playlist.start_task("fresh")

    assert spotify_playlist.get_task("stale") is None
    assert spotify_playlist.get_task("fresh") is not None


def test_a_task_still_being_updated_is_not_evicted(monkeypatch):
    """
    A long job must outlive the TTL as long as it is still reporting progress -
    eviction is for abandoned entries, not slow ones.
    """
    monkeypatch.setattr(spotify_playlist, "TASK_TTL_SECONDS", 0.05)
    spotify_playlist.start_task("long-job")

    time.sleep(0.1)
    spotify_playlist.update_task("long-job", progress=50)
    spotify_playlist.start_task("unrelated")

    assert spotify_playlist.get_task("long-job") is not None
