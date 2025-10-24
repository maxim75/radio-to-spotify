from flask import Flask, request, redirect, session, url_for
import logging
import subprocess
import os
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import load_playlist
import playlist_upload
import pandas as pd 
import datetime
import spotify_playlist
from urllib.parse import urlencode

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")

# Set up logging
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

# Determine log directory based on environment
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)

# Create file handler
file_handler = logging.FileHandler(os.path.join(log_dir, 'app.log'))
file_handler.setLevel(logging.DEBUG)

# Create formatters and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Configure root logger
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[console_handler, file_handler]
)

logging.info('app.py script started')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')  # Set a secret key for session management

def my_scheduled_job():
    load_playlist_route()

@app.route('/spotify/auth')
def spotify_auth():
    """Initiate Spotify OAuth flow"""
    try:
        auth_url = spotify_playlist.get_auth_url()
        return redirect(auth_url)
    except Exception as e:
        logging.error(f"Error initiating Spotify auth: {e}")
        return f"Error initiating Spotify authentication: {str(e)}", 500

@app.route('/callback')
def spotify_callback():
    """Handle Spotify OAuth callback"""
    try:
        # Get the authorization code from the request
        code = request.args.get('code')
        error = request.args.get('error')

        if error:
            logging.error(f"Spotify auth error: {error}")
            return f"Authentication error: {error}", 400

        if not code:
            logging.error("No authorization code received from Spotify")
            return "No authorization code received", 400

        # Exchange the code for access token
        success = spotify_playlist.handle_oauth_callback(code)
        
        if success:
            return "Successfully authenticated with Spotify! You can close this window."
        else:
            return "Failed to complete authentication", 400

    except Exception as e:
        logging.error(f"Error in Spotify callback: {e}")
        return f"Error processing callback: {str(e)}", 500


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

@app.route('/create_playlists')
def create_playlists():
    """Create Spotify playlists from S3 CSV files"""
    try:
        spotify_playlist.process_s3_playlists()
        return "Playlists creation process started"
    except Exception as e:
        logging.error(f"Error in create_playlists route: {e}")
        return f"Error creating playlists: {str(e)}", 500

if __name__ == '__main__':
    # In development, use flask run command instead
    # This will only be used when running python app.py directly
    app.run(host='0.0.0.0', port=8001, debug=True)