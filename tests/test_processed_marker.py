"""
The record of which stored CSVs have already been pushed to Spotify.

The distinction that matters here is between "the marker says nothing has been
processed" and "the marker could not be read". The first means process everything;
the second must not, because silently treating an S3 read failure as an empty set
would re-scan the whole bucket and then overwrite the real record with a partial one.
"""

import json

import playlist_batch


def test_the_marker_key_is_not_mistaken_for_a_playlist():
    assert playlist_batch.parse_playlist_key(playlist_batch.PROCESSED_MARKER_KEY) is None


def test_a_valid_marker_is_read(monkeypatch):
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: json.dumps({"processed": ["a.csv", "b.csv"], "updated_at": "x"}),
    )

    assert playlist_batch.load_processed_keys("bucket") == {"a.csv", "b.csv"}


def test_an_unreadable_marker_is_distinguished_from_an_empty_one(monkeypatch):
    monkeypatch.setattr(playlist_batch, "download_file_from_s3", lambda bucket, key: None)

    assert playlist_batch.load_processed_keys("bucket") is None


def test_an_empty_marker_reads_as_an_empty_set(monkeypatch):
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: json.dumps({"processed": [], "updated_at": "x"}),
    )

    assert playlist_batch.load_processed_keys("bucket") == set()


def test_a_malformed_marker_reads_as_unreadable(monkeypatch):
    monkeypatch.setattr(playlist_batch, "download_file_from_s3", lambda bucket, key: "{not json")

    assert playlist_batch.load_processed_keys("bucket") is None


def test_a_marker_with_a_wrong_shape_reads_as_unreadable(monkeypatch):
    monkeypatch.setattr(
        playlist_batch, "download_file_from_s3",
        lambda bucket, key: json.dumps({"processed": "a.csv"}),
    )

    assert playlist_batch.load_processed_keys("bucket") is None


def test_saving_writes_sorted_keys_and_a_timestamp(monkeypatch):
    written = {}
    monkeypatch.setattr(
        playlist_batch, "put_object_to_s3",
        lambda bucket, key, content: written.update(bucket=bucket, key=key, content=content) or True,
    )

    assert playlist_batch.save_processed_keys({"b.csv", "a.csv"}, "bucket") is True

    assert written["key"] == playlist_batch.PROCESSED_MARKER_KEY
    payload = json.loads(written["content"])
    assert payload["processed"] == ["a.csv", "b.csv"]
    assert payload["updated_at"]


def test_saving_reports_an_s3_failure(monkeypatch):
    monkeypatch.setattr(playlist_batch, "put_object_to_s3", lambda bucket, key, content: False)

    assert playlist_batch.save_processed_keys({"a.csv"}, "bucket") is False
