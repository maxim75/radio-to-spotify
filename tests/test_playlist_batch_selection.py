"""
Which stored CSVs a batch run covers.

The date in a filename is the date the playlist was *loaded*, and that - not the
play time inside the file - is what the endpoint's range selects. Getting the
regex wrong means either missing files or sweeping in the marker object.
"""

import datetime

import pytest

import playlist_batch


def test_a_well_formed_key_parses():
    assert playlist_batch.parse_playlist_key(
        "playlist_16134_20260814_072254.csv"
    ) == ("16134", "20260814")


def test_a_slug_station_id_parses():
    assert playlist_batch.parse_playlist_key(
        "playlist_retrofm_20260814_072254.csv"
    ) == ("retrofm", "20260814")


def test_a_station_id_containing_an_underscore_parses():
    assert playlist_batch.parse_playlist_key(
        "playlist_retro_fm_20260814_072254.csv"
    ) == ("retro_fm", "20260814")


@pytest.mark.parametrize("key", [
    "processed_playlists.json",
    "playlist_16134_20260814.csv",
    "playlist_16134_2026081_072254.csv",
    "playlist__20260814_072254.csv",
    "notaplaylist.csv",
    "playlist_16134_20260814_072254.txt",
])
def test_keys_that_are_not_playlists_do_not_parse(key):
    assert playlist_batch.parse_playlist_key(key) is None


def test_parse_iso_date_accepts_the_api_format():
    assert playlist_batch.parse_iso_date("2026-08-14") == datetime.date(2026, 8, 14)


@pytest.mark.parametrize("value", ["14-08-2026", "2026/08/14", "20260814", "", "tomorrow"])
def test_parse_iso_date_rejects_anything_else(value):
    with pytest.raises(ValueError):
        playlist_batch.parse_iso_date(value)


KEYS = [
    "playlist_16134_20260813_235959.csv",
    "playlist_16134_20260814_072254.csv",
    "playlist_16134_20260815_072254.csv",
    "playlist_16134_20260816_072254.csv",
    "playlist_16134_20260817_072254.csv",
    "playlist_38225_20260815_072254.csv",
    "playlist_retrofm_20260815_072254.csv",
    "processed_playlists.json",
]


def test_both_range_endpoints_are_inclusive():
    selected = playlist_batch.select_keys(
        KEYS, "16134", datetime.date(2026, 8, 14), datetime.date(2026, 8, 16)
    )

    assert selected == [
        "playlist_16134_20260814_072254.csv",
        "playlist_16134_20260815_072254.csv",
        "playlist_16134_20260816_072254.csv",
    ]


def test_only_the_requested_station_is_selected():
    selected = playlist_batch.select_keys(
        KEYS, "38225", datetime.date(2026, 8, 15), datetime.date(2026, 8, 15)
    )

    assert selected == ["playlist_38225_20260815_072254.csv"]


def test_a_slug_station_is_selected_like_any_other():
    selected = playlist_batch.select_keys(
        KEYS, "retrofm", datetime.date(2026, 8, 15), datetime.date(2026, 8, 15)
    )

    assert selected == ["playlist_retrofm_20260815_072254.csv"]


def test_an_int_station_id_matches_a_string_key():
    selected = playlist_batch.select_keys(
        KEYS, 16134, datetime.date(2026, 8, 15), datetime.date(2026, 8, 15)
    )

    assert selected == ["playlist_16134_20260815_072254.csv"]


def test_a_range_covering_nothing_selects_nothing():
    assert playlist_batch.select_keys(
        KEYS, "16134", datetime.date(2026, 1, 1), datetime.date(2026, 1, 2)
    ) == []


def test_selection_is_sorted():
    selected = playlist_batch.select_keys(
        list(reversed(KEYS)), "16134", datetime.date(2026, 8, 13), datetime.date(2026, 8, 17)
    )

    assert selected == sorted(selected)
