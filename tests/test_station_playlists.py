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


# --- Structured records: station id, playlist name and which scraper feeds it ---


def test_the_object_form_is_loaded_as_records(tmp_path):
    path = write_config(tmp_path, {
        "retrofm": {"playlist_name": "Retro FM", "source": "radiotut"},
        "16134": {"playlist_name": "Radio FM", "source": "radoxo"},
    })

    assert station_playlists.load_stations(path) == [
        {"station_id": "retrofm", "playlist_name": "Retro FM", "source": "radiotut"},
        {"station_id": "16134", "playlist_name": "Radio FM", "source": "radoxo"},
    ]


def test_the_legacy_string_form_infers_its_source(tmp_path):
    # Files written before the source field existed hold a bare playlist name. The
    # only non-numeric id in that world was the radiotut slug retrofm, so the digits
    # test reproduces the previous behaviour exactly rather than guessing.
    path = write_config(tmp_path, {"retrofm": "Retro FM", "16134": "Radio FM"})

    assert station_playlists.load_stations(path) == [
        {"station_id": "retrofm", "playlist_name": "Retro FM", "source": "radiotut"},
        {"station_id": "16134", "playlist_name": "Radio FM", "source": "radoxo"},
    ]


def test_a_record_with_an_unknown_source_is_skipped(tmp_path):
    # Scraping dispatches on this field; an unrecognised value has no scraper behind
    # it, so keeping the entry would only produce a failure every night.
    path = write_config(tmp_path, {
        "16134": {"playlist_name": "Radio FM", "source": "radoxo"},
        "999": {"playlist_name": "Nowhere FM", "source": "myspace"},
    })

    assert [s["station_id"] for s in station_playlists.load_stations(path)] == ["16134"]


def test_a_record_missing_its_source_defaults_by_id_shape(tmp_path):
    path = write_config(tmp_path, {"16134": {"playlist_name": "Radio FM"}})

    assert station_playlists.load_stations(path)[0]["source"] == "radoxo"


def test_records_without_a_usable_playlist_name_are_skipped(tmp_path):
    path = write_config(tmp_path, {
        "16134": {"playlist_name": "  Radio FM  ", "source": "radoxo"},
        "38225": {"playlist_name": "", "source": "radoxo"},
        "38234": {"source": "radoxo"},
        "38299": ["Radio FM"],
    })

    assert station_playlists.load_stations(path) == [
        {"station_id": "16134", "playlist_name": "Radio FM", "source": "radoxo"},
    ]


def test_an_unreadable_file_yields_no_records(tmp_path):
    assert station_playlists.load_stations(str(tmp_path / "nope.json")) == []


def test_load_config_still_answers_the_flat_map_for_both_forms(tmp_path):
    # Every existing caller (the batch endpoint, playlist_batch) reads this shape.
    path = write_config(tmp_path, {
        "retrofm": "Retro FM",
        "16134": {"playlist_name": "Radio FM", "source": "radoxo"},
    })

    assert station_playlists.load_config(path) == {
        "retrofm": "Retro FM",
        "16134": "Radio FM",
    }


# --- Saving ---


def test_saved_records_round_trip(tmp_path):
    path = str(tmp_path / "station_playlists.json")
    records = [
        {"station_id": "retrofm", "playlist_name": "Retro FM", "source": "radiotut"},
        {"station_id": "16134", "playlist_name": "Радио FM", "source": "radoxo"},
    ]

    assert station_playlists.save_stations(records, path) is True
    assert station_playlists.load_stations(path) == records


def test_the_saved_file_stays_hand_editable(tmp_path):
    path = str(tmp_path / "station_playlists.json")
    station_playlists.save_stations(
        [{"station_id": "16134", "playlist_name": "Радио FM", "source": "radoxo"}], path
    )

    text = open(path, encoding="utf-8").read()
    # Indented and not \u-escaped, because this file is still edited by hand; and
    # readable, unlike the 0600 token store this write path is otherwise copied from.
    assert "\n  " in text
    assert "Радио FM" in text
    assert os.stat(path).st_mode & 0o004


def test_a_save_leaves_no_temp_file_behind(tmp_path):
    path = str(tmp_path / "station_playlists.json")
    station_playlists.save_stations(
        [{"station_id": "16134", "playlist_name": "Radio FM", "source": "radoxo"}], path
    )

    assert [p.name for p in tmp_path.iterdir()] == ["station_playlists.json"]


def test_a_failed_save_reports_false_and_cleans_up(tmp_path, monkeypatch):
    path = str(tmp_path / "station_playlists.json")
    monkeypatch.setattr(station_playlists.os, "replace", _raise_oserror)

    assert station_playlists.save_stations([], path) is False
    assert list(tmp_path.iterdir()) == []


def _raise_oserror(*args, **kwargs):
    raise OSError("disk full")


# --- Bootstrapping the configured path from the file shipped in the image ---


def test_bootstrap_seeds_a_missing_config_from_the_default(tmp_path, monkeypatch):
    # Pointing STATION_PLAYLISTS_CONFIG at the empty /var/data volume must not mean
    # "no stations": that now stops the nightly scrape as well as the Spotify push.
    default = write_config(tmp_path, {"16134": "Radio FM"})
    target = str(tmp_path / "volume" / "station_playlists.json")
    monkeypatch.setattr(station_playlists, "DEFAULT_CONFIG_PATH", default)
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", target)

    station_playlists.bootstrap_config()

    assert station_playlists.load_config(target) == {"16134": "Radio FM"}


def test_bootstrap_never_clobbers_an_existing_config(tmp_path, monkeypatch):
    default = write_config(tmp_path, {"16134": "Radio FM"})
    target = tmp_path / "volume"
    target.mkdir()
    existing = write_config(target, {"38225": "Edited By Hand"})
    monkeypatch.setattr(station_playlists, "DEFAULT_CONFIG_PATH", default)
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", existing)

    station_playlists.bootstrap_config()

    assert station_playlists.load_config(existing) == {"38225": "Edited By Hand"}


def test_bootstrap_does_nothing_when_the_config_is_the_default(tmp_path, monkeypatch):
    default = str(tmp_path / "station_playlists.json")
    monkeypatch.setattr(station_playlists, "DEFAULT_CONFIG_PATH", default)
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", default)

    station_playlists.bootstrap_config()

    assert not os.path.exists(default)
