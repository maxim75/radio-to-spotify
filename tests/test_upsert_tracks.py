"""
Upserting tracks into a playlist that already exists and already has content.

The point of this function is that running it twice adds nothing the second time:
duplicates are dropped both within the batch and against what the playlist already
holds. Tracks Spotify cannot find are reported rather than silently dropped.
"""

import os

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import spotify_playlist


class FakeSpotify:
    """
    A minimal stand-in for spotipy.Spotify.

    `catalogue` maps "song|artist" to a URI; anything absent is a search miss.
    """

    def __init__(self, playlists=None, tracks=None, catalogue=None):
        self.playlists = playlists or []
        self.tracks = tracks or {}
        self.catalogue = catalogue or {}
        self.created = []
        self.added = []

    def current_user(self):
        return {"id": "me"}

    def user_playlists(self, user_id):
        return {"items": self.playlists, "next": None}

    def next(self, results):
        return None

    def user_playlist_create(self, user_id, name, public=False):
        playlist = {"id": f"new-{name}", "name": name, "owner": {"id": user_id}}
        self.created.append(playlist)
        self.playlists.append(playlist)
        return playlist

    def playlist_tracks(self, playlist_id):
        return {"items": self.tracks.get(playlist_id, []), "next": None}

    def playlist_add_items(self, playlist_id, uris):
        self.added.append((playlist_id, list(uris)))

    def search(self, q, type="track", limit=1):
        uri = self.catalogue.get(q)
        if not uri:
            return {"tracks": {"items": []}}
        return {"tracks": {"items": [{"uri": uri}]}}


def track_item(uri, name="n", artist="a"):
    return {"track": {"id": uri, "name": name, "uri": uri,
                      "artists": [{"name": artist}], "album": {"name": "al"}}}


def test_a_missing_playlist_is_created():
    sp = FakeSpotify(catalogue={"Song A artist:Artist A": "spotify:track:1"})

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")]
    )

    assert [p["name"] for p in sp.created] == ["Radio FM"]
    assert result["playlist_id"] == "new-Radio FM"
    assert result["added"] == 1


def test_an_existing_playlist_is_reused():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")]
    )

    assert sp.created == []
    assert result["playlist_id"] == "pl1"


def test_a_playlist_owned_by_someone_else_is_not_reused():
    # A followed playlist with the same name cannot be modified, so matching it would
    # make every add fail.
    sp = FakeSpotify(
        playlists=[{"id": "other", "name": "Radio FM", "owner": {"id": "someone-else"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")]
    )

    assert result["playlist_id"] == "new-Radio FM"


def test_tracks_already_in_the_playlist_are_skipped():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        tracks={"pl1": [track_item("spotify:track:1")]},
        catalogue={
            "Song A artist:Artist A": "spotify:track:1",
            "Song B artist:Artist B": "spotify:track:2",
        },
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A"), ("Artist B", "Song B")]
    )

    assert sp.added == [("pl1", ["spotify:track:2"])]
    assert result["added"] == 1
    assert result["skipped_existing"] == 1


def test_two_names_resolving_to_the_same_uri_are_added_once():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={
            "Song A artist:Artist A": "spotify:track:1",
            "Song A (Remastered) artist:Artist A": "spotify:track:1",
        },
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM",
        [("Artist A", "Song A"), ("Artist A", "Song A (Remastered)")],
    )

    assert sp.added == [("pl1", ["spotify:track:1"])]
    assert result["added"] == 1
    assert result["skipped_existing"] == 1


def test_unmatched_tracks_are_reported_in_full():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )

    result = spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A"), ("Artist Z", "Song Z")]
    )

    assert result["unmatched"] == [{"artist": "Artist Z", "song": "Song Z"}]
    assert result["added"] == 1
    assert result["skipped_existing"] == 0


def test_adds_are_batched_at_the_spotify_limit_of_100():
    catalogue = {f"Song {i} artist:Artist": f"spotify:track:{i}" for i in range(250)}
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue=catalogue,
    )

    spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist", f"Song {i}") for i in range(250)]
    )

    assert [len(uris) for _, uris in sp.added] == [100, 100, 50]


def test_an_empty_batch_adds_nothing():
    sp = FakeSpotify(playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}])

    result = spotify_playlist.upsert_tracks_into_playlist(sp, "Radio FM", [])

    assert sp.added == []
    assert result == {
        "playlist_id": "pl1",
        "playlist_name": "Radio FM",
        "added": 0,
        "skipped_existing": 0,
        "unmatched": [],
    }


def test_progress_is_reported_when_a_task_id_is_given():
    sp = FakeSpotify(
        playlists=[{"id": "pl1", "name": "Radio FM", "owner": {"id": "me"}}],
        catalogue={"Song A artist:Artist A": "spotify:track:1"},
    )
    spotify_playlist.reset_tasks()
    spotify_playlist.start_task("task-1")

    spotify_playlist.upsert_tracks_into_playlist(
        sp, "Radio FM", [("Artist A", "Song A")], task_id="task-1"
    )

    assert spotify_playlist.get_task("task-1")["progress"] >= 75


def test_get_playlist_tracks_reads_from_a_client():
    sp = FakeSpotify(tracks={"pl1": [track_item("spotify:track:1", name="N", artist="A")]})

    tracks = spotify_playlist.get_playlist_tracks(sp, "pl1")

    assert [t["uri"] for t in tracks] == ["spotify:track:1"]
    assert tracks[0]["name"] == "N"
    assert tracks[0]["artist"] == "A"
