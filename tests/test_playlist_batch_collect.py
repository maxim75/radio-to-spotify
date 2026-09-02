"""
Reading tracks out of the selected CSVs.

A radio station plays the same song many times a day, so the batch is deduped
before a single Spotify search is spent on it. The bucket also still holds
one-byte files from before the scrapers raised on an empty scrape, and those
must be skipped rather than crashing a run.
"""

import playlist_batch


HEADER = "time,artist_name,song_name\n"


def fake_bucket(monkeypatch, contents):
    """Serve canned CSV bodies for keys; unknown keys behave like a failed download."""
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: contents.get(key),
    )


def test_tracks_are_collected_in_order(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n2026-08-14T10:05:00,Artist B,Song B\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [
        ("Artist A", "Song A"),
        ("Artist B", "Song B"),
    ]


def test_repeats_within_a_file_are_deduped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n2026-08-14T14:00:00,Artist A,Song A\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Artist A", "Song A")]


def test_repeats_across_files_are_deduped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n",
        "b.csv": HEADER + "2026-08-15T10:00:00,Artist A,Song A\n2026-08-15T11:00:00,Artist C,Song C\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv", "b.csv"]) == [
        ("Artist A", "Song A"),
        ("Artist C", "Song C"),
    ]


def test_cyrillic_track_names_survive(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Кино,Группа крови\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Кино", "Группа крови")]


def test_an_empty_file_is_skipped(monkeypatch):
    fake_bucket(monkeypatch, {
        "empty.csv": "\n",
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["empty.csv", "a.csv"]) == [
        ("Artist A", "Song A")
    ]


def test_a_failed_download_is_skipped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + "2026-08-14T10:00:00,Artist A,Song A\n",
    })

    assert playlist_batch.collect_tracks("bucket", ["missing.csv", "a.csv"]) == [
        ("Artist A", "Song A")
    ]


def test_rows_with_a_missing_artist_or_song_are_skipped(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + (
            "2026-08-14T10:00:00,,Song A\n"
            "2026-08-14T10:05:00,Artist B,\n"
            "2026-08-14T10:10:00,Artist C,Song C\n"
        ),
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Artist C", "Song C")]


def test_surrounding_whitespace_is_trimmed(monkeypatch):
    fake_bucket(monkeypatch, {
        "a.csv": HEADER + '2026-08-14T10:00:00,"  Artist A  ","  Song A  "\n',
    })

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == [("Artist A", "Song A")]


def test_a_file_with_no_rows_yields_nothing(monkeypatch):
    fake_bucket(monkeypatch, {"a.csv": HEADER})

    assert playlist_batch.collect_tracks("bucket", ["a.csv"]) == []
