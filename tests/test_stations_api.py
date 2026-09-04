"""
The /api/stations CRUD used by the configuration page.

This config now drives the nightly scrape as well as the Spotify mapping, so a bad
record does more damage than a mistyped playlist name: an id with a space in it
becomes an S3 key nobody can parse back, and an unknown source has no scraper behind
it. Validation therefore happens on the way in, not on the way out.
"""

import json
import os

import pytest

os.environ.setdefault("BASIC_AUTH_DISABLED", "true")
os.environ.setdefault("PLAYLIST_DATA_DIR", "/tmp")

import app as flask_app
import station_playlists


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "station_playlists.json"
    path.write_text(
        json.dumps({"16134": {"playlist_name": "Radio FM", "source": "radoxo"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(station_playlists, "CONFIG_PATH", str(path))
    return path


@pytest.fixture
def client(config_path):
    flask_app.app.config.update(TESTING=True)
    return flask_app.app.test_client()


def stored(config_path):
    return json.loads(config_path.read_text(encoding="utf-8"))


def test_the_configured_stations_are_listed_with_their_sources(client):
    response = client.get("/api/stations")

    assert response.status_code == 200
    assert response.json["stations"] == [
        {"station_id": "16134", "playlist_name": "Radio FM", "source": "radoxo"}
    ]
    # The edit form builds its source dropdown from this rather than hardcoding the
    # list in two places.
    assert response.json["sources"] == list(station_playlists.SOURCES)


def test_a_station_can_be_added(client, config_path):
    response = client.post("/api/stations", json={
        "station_id": "retrofm", "playlist_name": "Retro FM", "source": "radiotut"
    })

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert stored(config_path)["retrofm"] == {
        "playlist_name": "Retro FM", "source": "radiotut"
    }
    # The existing entry is still there - a save rewrites the whole file.
    assert "16134" in stored(config_path)


def test_adding_a_station_that_already_exists_is_rejected(client, config_path):
    response = client.post("/api/stations", json={
        "station_id": "16134", "playlist_name": "Something Else", "source": "radoxo"
    })

    assert response.status_code == 400
    assert "already" in response.json["message"].lower()
    assert stored(config_path)["16134"]["playlist_name"] == "Radio FM"


def test_a_station_can_be_edited(client, config_path):
    response = client.put("/api/stations/16134", json={
        "station_id": "16134", "playlist_name": "Radio Renamed", "source": "radiotut"
    })

    assert response.status_code == 200
    assert stored(config_path)["16134"] == {
        "playlist_name": "Radio Renamed", "source": "radiotut"
    }


def test_editing_can_rename_the_station_id_in_place(client, config_path):
    # In place: the config is ordered and the page lists it in file order, so a rename
    # must not move the station to the bottom of the list.
    client.post("/api/stations", json={
        "station_id": "38225", "playlist_name": "Radio 38225", "source": "radoxo"
    })

    response = client.put("/api/stations/16134", json={
        "station_id": "16135", "playlist_name": "Radio FM", "source": "radoxo"
    })

    assert response.status_code == 200
    assert list(stored(config_path)) == ["16135", "38225"]


def test_renaming_onto_another_station_is_rejected(client, config_path):
    client.post("/api/stations", json={
        "station_id": "38225", "playlist_name": "Radio 38225", "source": "radoxo"
    })

    response = client.put("/api/stations/16134", json={
        "station_id": "38225", "playlist_name": "Radio FM", "source": "radoxo"
    })

    assert response.status_code == 400
    assert stored(config_path)["38225"]["playlist_name"] == "Radio 38225"


def test_a_station_can_be_deleted(client, config_path):
    response = client.delete("/api/stations/16134")

    assert response.status_code == 200
    assert stored(config_path) == {}


def test_editing_or_deleting_an_unknown_station_is_a_404(client):
    assert client.put("/api/stations/99999", json={
        "station_id": "99999", "playlist_name": "Nowhere FM", "source": "radoxo"
    }).status_code == 404
    assert client.delete("/api/stations/99999").status_code == 404


@pytest.mark.parametrize("body, reason", [
    ({"playlist_name": "Radio FM", "source": "radoxo"}, "no station id"),
    ({"station_id": "  ", "playlist_name": "Radio FM", "source": "radoxo"}, "blank station id"),
    ({"station_id": "radio fm", "playlist_name": "Radio FM", "source": "radoxo"}, "space in the id"),
    ({"station_id": "../etc", "playlist_name": "Radio FM", "source": "radoxo"}, "path traversal"),
    ({"station_id": "x" * 65, "playlist_name": "Radio FM", "source": "radoxo"}, "id too long"),
    ({"station_id": "38225", "playlist_name": "  ", "source": "radoxo"}, "blank playlist name"),
    ({"station_id": "38225", "playlist_name": "x" * 101, "source": "radoxo"}, "name over Spotify's cap"),
    ({"station_id": "38225", "playlist_name": "Radio FM", "source": "myspace"}, "unknown source"),
    ({"station_id": "38225", "playlist_name": "Radio FM"}, "missing source"),
])
def test_invalid_records_are_rejected_without_touching_the_file(client, config_path, body, reason):
    response = client.post("/api/stations", json=body)

    assert response.status_code == 400, reason
    assert response.json["status"] == "error"
    assert list(stored(config_path)) == ["16134"], reason


def test_a_body_that_is_not_an_object_is_rejected(client):
    assert client.post("/api/stations", json=["16134"]).status_code == 400
    assert client.post(
        "/api/stations", data="{not json", content_type="application/json"
    ).status_code == 400


def test_a_failed_write_is_a_500_not_a_silent_success(client, monkeypatch):
    # save_stations swallows its own exceptions, so its return value is the only sign
    # the edit never reached disk. Reporting success there would leave the page showing
    # a station that vanishes on the next reload.
    monkeypatch.setattr(station_playlists, "save_stations", lambda *a, **kw: False)

    response = client.post("/api/stations", json={
        "station_id": "38225", "playlist_name": "Radio 38225", "source": "radoxo"
    })

    assert response.status_code == 500
    assert response.json["status"] == "error"


def test_the_config_page_routes_render_the_react_app(client):
    # Client-side routing has to survive a hard reload on the edit page, which means
    # Flask must answer /stations/<id> with the same template.
    assert client.get("/stations").status_code == 200
    assert b'id="root"' in client.get("/stations/16134").data


def test_the_batch_form_endpoint_still_sees_the_new_format(client):
    response = client.get("/api/station-playlists")

    assert response.json["stations"] == {"16134": "Radio FM"}
