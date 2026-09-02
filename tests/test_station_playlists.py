"""
Loading the station-to-playlist map.

A broken config file must never take the app down: the endpoint answers 400
"not configured" rather than 500, so every failure path here returns a mapping
rather than raising.
"""

import json

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
