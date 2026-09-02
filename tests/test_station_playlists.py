"""
Loading the station-to-playlist map.

A broken config file must never take the app down: the endpoint answers 400
"not configured" rather than 500, so every failure path here returns a mapping
rather than raising.
"""

import importlib
import json
import os

import station_playlists


def write_config(tmp_path, payload):
    path = tmp_path / "station_playlists.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_a_valid_config_is_loaded(tmp_path):
    path = write_config(tmp_path, {"16134": "Radio FM", "retrofm": "Retro FM"})

    assert station_playlists.load_config(path) == {
        "16134": "Radio FM",
        "retrofm": "Retro FM",
    }


def test_station_ids_are_strings_even_when_written_as_numbers(tmp_path):
    # json.dumps writes an int key as a string, but a hand-edited file or a future
    # caller may pass an int; nothing downstream may assume a numeric id, because
    # retrofm is a slug.
    path = write_config(tmp_path, {16134: "Radio FM"})

    assert station_playlists.load_config(path) == {"16134": "Radio FM"}


def test_a_missing_file_yields_an_empty_mapping(tmp_path):
    assert station_playlists.load_config(str(tmp_path / "nope.json")) == {}


def test_malformed_json_yields_an_empty_mapping(tmp_path):
    path = tmp_path / "station_playlists.json"
    path.write_text("{not json", encoding="utf-8")

    assert station_playlists.load_config(str(path)) == {}


def test_a_non_object_top_level_yields_an_empty_mapping(tmp_path):
    path = write_config(tmp_path, ["16134", "Radio FM"])

    assert station_playlists.load_config(path) == {}


def test_invalid_entries_are_skipped_without_discarding_the_file(tmp_path):
    path = write_config(tmp_path, {
        "16134": "Radio FM",
        "38225": "",
        "38234": None,
        "retrofm": "   Retro FM   ",
    })

    assert station_playlists.load_config(path) == {
        "16134": "Radio FM",
        "retrofm": "Retro FM",
    }


def test_get_playlist_name_returns_none_for_an_unconfigured_station(tmp_path, monkeypatch):
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", write_config(tmp_path, {"16134": "Radio FM"}))

    assert station_playlists.get_playlist_name("16134") == "Radio FM"
    assert station_playlists.get_playlist_name(16134) == "Radio FM"
    assert station_playlists.get_playlist_name("99999") is None


def test_get_configured_stations_reads_the_default_path(tmp_path, monkeypatch):
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", write_config(tmp_path, {"16134": "Radio FM"}))

    assert station_playlists.get_configured_stations() == {"16134": "Radio FM"}


def test_an_empty_env_var_still_resolves_to_the_default_path(monkeypatch):
    # os.environ.get(NAME, default) treats a *present but empty* var as set, which
    # is exactly what .env.template used to ship uncommented - CONFIG_PATH became ""
    # and open("") raised FileNotFoundError on every call. `or` must be used instead
    # so an empty string falls through to the default, the same way
    # spotify_token_store.token_path() already handles SPOTIFY_TOKEN_STORE.
    monkeypatch.setenv("STATION_PLAYLISTS_CONFIG", "")
    default_path = os.path.join(
        os.path.dirname(os.path.abspath(station_playlists.__file__)), "station_playlists.json"
    )

    reloaded = importlib.reload(station_playlists)
    try:
        assert reloaded.CONFIG_PATH == default_path
    finally:
        monkeypatch.delenv("STATION_PLAYLISTS_CONFIG", raising=False)
        importlib.reload(station_playlists)


def test_an_explicit_empty_path_fails_loudly_instead_of_falling_back(tmp_path, monkeypatch):
    # load_config's `path = path or CONFIG_PATH` used to treat an explicitly-passed
    # "" the same as "not passed" and silently read the default file instead. Only
    # None means "not passed" - an explicit "" must be attempted (and fail) as given.
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", write_config(tmp_path, {"16134": "Radio FM"}))

    assert station_playlists.load_config("") == {}
