import datetime
import pandas as pd 
import logging
import os
import datetime
from bs4 import BeautifulSoup
from latest_user_agents import get_latest_user_agents
from zoneinfo import ZoneInfo
import requests

aws_api_key = os.environ.get("AWS_API_KEY")

def get_playlist_from_radiotut(station_id, day):
    timezone_name = "Europe/Moscow"
    current_datetime = datetime.datetime.now(ZoneInfo(timezone_name))
    chrome_user_agents = get_latest_user_agents()
    user_agent = chrome_user_agents[0]
    tracks = []

    url = f"https://radiotut.com/radio/{station_id}/playlist/{day if day != 1 else ""}/"
    print(url)

    response = requests.get(url, headers={"user-agent": user_agent})
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

def load_playlist():
    station_id = "retrofm"
    print(f"load_playlist {datetime.datetime.now()}")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    playlist_df = get_playlist_from_radiotut(station_id, 2);
    filename = f"/var/data/playlist_{station_id}_{timestamp}.csv"
    playlist_df.to_csv(filename, index=False)
    return filename
