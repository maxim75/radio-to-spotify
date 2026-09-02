"""
put_object_to_s3 follows playlist_upload's swallow-and-return-bool contract.
"""

import playlist_upload


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def put_object(self, Bucket, Key, Body):
        if self.fail:
            raise RuntimeError("s3 is down")
        self.calls.append((Bucket, Key, Body))


def test_a_string_body_is_written_as_utf8(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(playlist_upload.boto3, "client", lambda *a, **k: client)

    assert playlist_upload.put_object_to_s3("bucket", "key.json", "Группа") is True
    assert client.calls == [("bucket", "key.json", "Группа".encode("utf-8"))]


def test_a_failure_is_reported_by_return_value_not_raised(monkeypatch):
    monkeypatch.setattr(playlist_upload.boto3, "client", lambda *a, **k: FakeClient(fail=True))

    assert playlist_upload.put_object_to_s3("bucket", "key.json", "x") is False
