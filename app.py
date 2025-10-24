from flask import Flask
import logging
import subprocess
import os
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import load_playlist
import playlist_upload
import pandas as pd 
import datetime
import spotipy
from spotipy.oauth2 import SpotifyOAuth


AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
FLASK_ENV = os.environ.get("FLASK_ENV")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

# Create a StreamHandler for console output
console_handler = logging.StreamHandler()

# Create a FileHandler for file output
if FLASK_ENV == "development":
    handlers=[console_handler]
else:
    file_handler = logging.FileHandler('/var/log/app.log')
    handlers=[console_handler, file_handler]

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=handlers
                    )

logging.info('app.py script started')
logging.info(f"SPOTIFY_CLIENT_ID: {SPOTIFY_CLIENT_ID}")
logging.info(f"SPOTIFY_CLIENT_SECRET: {SPOTIFY_CLIENT_SECRET}")

scope = "user-library-read playlist-modify-private playlist-modify-public"
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=scope, client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET, redirect_uri="http://max.kozlenko.info"))


app = Flask(__name__)

def my_scheduled_job():
    load_playlist_route()


scheduler = BackgroundScheduler()
scheduler.add_job(func=my_scheduled_job, trigger="cron", hour="23", minute="40") 
scheduler.start()

atexit.register(lambda: scheduler.shutdown())

@app.route('/')
def hello():
    logging.warning('Hello, World! endpoint was reached')
    logging.info('INFO')
    logging.debug('debug')
    return "Radio to Spotify!!"

@app.route('/load_playlist')
def load_playlist_route():
    playlist_filename = load_playlist.load_playlist()
    playlist_upload.upload_file_to_s3(
        playlist_filename,
        "radio-playlists",
        playlist_filename.split("/")[-1]
    )


    for station_id in [75885, 309175, 294683]:
        current_datetime = datetime.datetime.now()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        current_date = current_datetime.strftime("%Y-%m-%d")
        yesterday_date = (current_datetime - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        playlist_df = load_playlist.get_playlist_from_raddio(station_id, yesterday_date)
        filename = f"/var/data/playlist_{station_id}_{timestamp}.csv"
        playlist_df.to_csv(filename, index=False)
        playlist_upload.upload_file_to_s3(
            filename,
            "radio-playlists",
            filename.split("/")[-1]
        )


    return "Playlist loaded"

@app.route('/config')
def config():
    if AWS_ACCESS_KEY_ID:
        return f"AWS_ACCESS_KEY_ID {AWS_ACCESS_KEY_ID[:4]}"
    else:
        return "AWS_ACCESS_KEY_ID is not set"
    
@app.route('/test')
def test():
    return "Test endpoint is working."

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=8001) 
	