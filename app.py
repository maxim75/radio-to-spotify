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

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")

# Create a StreamHandler for console output
console_handler = logging.StreamHandler()

# Create a FileHandler for file output
file_handler = logging.FileHandler('/var/log/app.log')

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[console_handler, file_handler]
                    )

logging.info('app.py script started')

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

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=8001)
	