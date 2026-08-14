import datetime
import pandas as pd 
import logging
import os
import datetime
from bs4 import BeautifulSoup
from latest_user_agents import get_latest_user_agents
from zoneinfo import ZoneInfo
import requests
import re

aws_api_key = os.environ.get("AWS_API_KEY")

# Where scraped CSVs are written before upload. /var/data only exists inside the
# container (docker-compose mounts ./data there); override for local runs and tests.
DATA_DIR = os.environ.get("PLAYLIST_DATA_DIR", "/var/data")

def get_user_agent():
    chrome_user_agents = get_latest_user_agents()
    user_agent = chrome_user_agents[0]
    return user_agent

def get_playlist_from_radiotut(station_id, day):
    timezone_name = "Europe/Moscow"
    current_datetime = datetime.datetime.now(ZoneInfo(timezone_name))
    tracks = []

    url = f"https://radiotut.com/radio/{station_id}/playlist/{day if day != 1 else ""}/"
    print(url)

    response = requests.get(url, headers={"user-agent": get_user_agent()})
    soup = BeautifulSoup(response.text, "html.parser")

    track_history_items = soup.select(".b_playlist li")
    for track_history_item in track_history_items:
        time = track_history_item.select(".time")[0].get_text().strip()
        artist_name = track_history_item.select(".artist_name")[0].get_text().strip()
        song_name = track_history_item.select(".song_name")[0].get_text().strip()
        current_date = (current_datetime - datetime.timedelta(days=day-1)).strftime("%Y-%m-%d")
        tracks.append({"time" : f"{current_date}T{time}:00", "artist_name": artist_name, "song_name": song_name})
        
    tracks_df = pd.DataFrame(tracks)
    return tracks_df

# The columns every scraper must return; create_playlist_from_csv depends on these names.
PLAYLIST_COLUMNS = ["time", "artist_name", "song_name"]

# raddio.net rebranded to Radoxo. The old
# /radio_stations/playlist/playlist?id=..&day=.. endpoint now 301s to the bare homepage,
# which is why scrapes silently produced nothing. The day view is this JSON endpoint,
# and its station ids are NOT the old raddio.net ids.
RADOXO_PLAYLIST_URL = "https://radoxo.com/playlist-for-day"

# Radoxo station ids to scrape nightly.
#
# EMPTY BY DESIGN. The previous values (75885, 309175, 294683) were raddio.net ids and do
# not carry over: 309175 and 294683 now 404, and 75885 resolves to an unrelated Brazilian
# station. Scraping them would collect the wrong data rather than no data.
#
# To repopulate, find each station on https://radoxo.com and run:
#     uv run python -c "import load_playlist; \
#         print(load_playlist.get_radoxo_station_id('https://radoxo.com/<country>/<slug>'))"
RADOXO_STATION_IDS = []

class NoTracksFoundError(Exception):
    """Raised when a scrape returns a page but no tracks, so breakage is never silent."""

def get_playlist_from_radoxo(station_id, date):
    """
    Fetch one day of playlist history for a Radoxo station.

    `date` is YYYY-MM-DD; timestamps come back as UTC epoch seconds. Radoxo keeps only
    about the last week, so older dates return nothing. Raises NoTracksFoundError rather
    than returning an empty DataFrame, so a broken selector or a dead station id fails
    loudly instead of writing an empty CSV.
    """
    url = f"{RADOXO_PLAYLIST_URL}?stationId={station_id}&day={date}"
    logging.info(f"Fetching Radoxo playlist: {url}")

    response = requests.get(
        url,
        headers={"user-agent": get_user_agent(), "x-requested-with": "XMLHttpRequest"},
        timeout=30,
    )
    # A retired station id returns 404 here; surface it instead of parsing an error page.
    response.raise_for_status()

    payload = response.json()
    soup = BeautifulSoup(payload.get("main", ""), "html.parser")

    tracks = []
    for item in soup.select("li.playlist-track"):
        played_at = item.select_one(".playlist-track__status[data-ts]")
        song_name = item.select_one(".playlist-track__song")
        artist_name = item.select_one(".playlist-track__artist")
        if not (played_at and song_name and artist_name):
            continue

        played = datetime.datetime.fromtimestamp(
            int(played_at["data-ts"]), ZoneInfo("Etc/UTC")
        )
        tracks.append({
            "time": played.strftime("%Y-%m-%dT%H:%M:%S"),
            "artist_name": artist_name.get_text().strip(),
            "song_name": song_name.get_text().strip(),
        })

    if not tracks:
        raise NoTracksFoundError(
            f"Radoxo station {station_id} returned no tracks for {date}. The station id "
            f"may be retired, the date outside the ~7 day window, or the markup changed."
        )

    logging.info(f"Radoxo station {station_id}: {len(tracks)} tracks for {date}")
    return pd.DataFrame(tracks, columns=PLAYLIST_COLUMNS)

def get_radoxo_station_id(station_page_url):
    """
    Look up a Radoxo numeric station id from its public page URL, e.g.
    https://radoxo.com/ukraine/xit-fm. Helper for reconfiguring STATION_IDS by hand.
    """
    response = requests.get(station_page_url, headers={"user-agent": get_user_agent()}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    day_link = soup.select_one("[data-station-id]")
    if not day_link:
        raise ValueError(f"No station id found on {station_page_url}")
    return int(day_link["data-station-id"])

def load_playlist():
    station_id = "retrofm"
    print(f"load_playlist {datetime.datetime.now()}")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    playlist_df = get_playlist_from_radiotut(station_id, 2);
    filename = os.path.join(DATA_DIR, f"playlist_{station_id}_{timestamp}.csv")
    playlist_df.to_csv(filename, index=False)
    return filename



