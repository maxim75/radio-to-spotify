from flask import Flask, Response, request, redirect, session, url_for, render_template, flash
import logging
import subprocess
import os
import hmac
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import load_playlist
import playlist_upload
import pandas as pd
import datetime
import spotify_playlist
from urllib.parse import urlencode
from io import StringIO
import uuid
import threading

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")

# HTTP Basic Auth credentials. Read after `import spotify_playlist`, which is what
# calls load_dotenv() and populates the environment from .env.
BASIC_AUTH_USERNAME = os.environ.get("BASIC_AUTH_USERNAME")
BASIC_AUTH_PASSWORD = os.environ.get("BASIC_AUTH_PASSWORD")
BASIC_AUTH_REALM = os.environ.get("BASIC_AUTH_REALM", "Radio to Spotify")
# Opt out entirely (local development only) with BASIC_AUTH_DISABLED=true.
BASIC_AUTH_DISABLED = os.environ.get("BASIC_AUTH_DISABLED", "").strip().lower() in (
    "1", "true", "yes", "on"
)

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

app = Flask(__name__, static_url_path='/static', static_folder='static/dist')
# `or` rather than a get() default: docker compose substitutes an unset variable as an
# empty string, and an empty secret key makes Flask refuse to open or save any session.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-key'
if not os.environ.get('FLASK_SECRET_KEY'):
    logging.warning(
        "FLASK_SECRET_KEY is not set - falling back to a default key that is public in "
        "this repository. Anyone can forge a session cookie, including the Spotify token "
        "it carries. Set FLASK_SECRET_KEY to a random value in production."
    )

# Configure session settings for security
app.config.update(
    SESSION_COOKIE_SECURE=False,  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=1)  # Session expires after 1 hour
)

if BASIC_AUTH_DISABLED:
    logging.warning("HTTP Basic Auth is DISABLED via BASIC_AUTH_DISABLED - do not use this in production")
elif not (BASIC_AUTH_USERNAME and BASIC_AUTH_PASSWORD):
    logging.error(
        "BASIC_AUTH_USERNAME/BASIC_AUTH_PASSWORD are not set - all requests will be "
        "rejected with 503. Set them in .env, or set BASIC_AUTH_DISABLED=true for local development."
    )

def check_basic_auth(auth):
    """Constant-time comparison of supplied Basic Auth credentials against the configured ones"""
    if not auth or auth.type != 'basic':
        return False

    # Compare both fields unconditionally (no short-circuit) so a wrong username and a
    # wrong password take the same amount of time.
    username_ok = hmac.compare_digest(
        (auth.username or '').encode('utf-8'), BASIC_AUTH_USERNAME.encode('utf-8')
    )
    password_ok = hmac.compare_digest(
        (auth.password or '').encode('utf-8'), BASIC_AUTH_PASSWORD.encode('utf-8')
    )
    return username_ok & password_ok

# Paths reachable without credentials. The container orchestrator (Coolify/Docker)
# probes the health endpoint without any way to send Basic Auth, so it is exempt.
# Keep the set minimal and keep those responses free of anything sensitive.
PUBLIC_PATHS = frozenset({'/health'})

# Returned when a route needs Spotify but the session holds no token. Without this the
# request reached spotipy, which fell back to its interactive console flow and died with
# "EOF when reading a line", surfacing as an opaque 500.
SPOTIFY_AUTH_REQUIRED = {
    'status': 'error',
    'message': 'Not authenticated with Spotify. Connect your account and try again.',
    'auth_url': '/spotify/auth',
}

@app.before_request
def require_basic_auth():
    """Require HTTP Basic Auth for every request, including static files and the OAuth callback"""
    if request.path in PUBLIC_PATHS:
        return None

    if BASIC_AUTH_DISABLED:
        return None

    # Fail closed: without configured credentials the app serves nothing rather than
    # silently running unprotected.
    if not (BASIC_AUTH_USERNAME and BASIC_AUTH_PASSWORD):
        return Response(
            'Server is not configured: BASIC_AUTH_USERNAME and BASIC_AUTH_PASSWORD must be set.',
            503,
            {'Content-Type': 'text/plain; charset=utf-8'}
        )

    if check_basic_auth(request.authorization):
        return None

    logging.warning(f"Rejected unauthenticated request to {request.path} from {request.remote_addr}")
    return Response(
        'Authentication required.',
        401,
        {
            'WWW-Authenticate': f'Basic realm="{BASIC_AUTH_REALM}", charset="UTF-8"',
            'Content-Type': 'text/plain; charset=utf-8'
        }
    )

def scrape_and_upload_playlists():
    """
    Scrape every configured source and upload the results to S3.

    A playlist is only uploaded when it actually contains tracks: an empty scrape is a
    bug in the scraper or a retired station, and writing it produced the 877 one-byte
    CSVs that accumulated after raddio.net became Radoxo. One failing station does not
    abort the others.

    Returns (uploaded, failures) where failures is a list of (source, reason).
    """
    uploaded = []
    failures = []

    try:
        # load_playlist() raises NoTracksFoundError on an empty scrape and only writes a
        # file when it has tracks, so there is nothing to re-read here. Reading it back
        # was worse than redundant: a bare open() uses the platform default encoding,
        # which is ASCII in the container, and the Cyrillic track names blew up on it.
        playlist_filename = load_playlist.load_playlist()
        playlist_upload.upload_file_to_s3(
            playlist_filename, "radio-playlists", playlist_filename.split("/")[-1]
        )
        uploaded.append(playlist_filename.split("/")[-1])
    except Exception as e:
        failures.append(("retrofm", str(e)))
        logging.error(f"Error scraping retrofm: {e}")

    current_datetime = datetime.datetime.now()
    yesterday_date = (current_datetime - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    for station_id in load_playlist.RADOXO_STATION_IDS:
        try:
            playlist_df = load_playlist.get_playlist_from_radoxo(station_id, yesterday_date)

            # Guard the upload itself as well, so a future scraper change that returns an
            # empty frame instead of raising still cannot write a junk file to S3.
            if playlist_df.empty:
                failures.append((str(station_id), "scrape produced no tracks - not uploaded"))
                logging.error(f"Station {station_id} produced no tracks - skipping upload")
                continue

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(load_playlist.DATA_DIR, f"playlist_{station_id}_{timestamp}.csv")
            playlist_df.to_csv(filename, index=False)
            playlist_upload.upload_file_to_s3(
                filename, "radio-playlists", filename.split("/")[-1]
            )
            uploaded.append(filename.split("/")[-1])
        except Exception as e:
            failures.append((str(station_id), str(e)))
            logging.error(f"Error scraping station {station_id}: {e}")

    return uploaded, failures

def my_scheduled_job():
    """Scheduled job to load playlists without Flask context"""
    try:
        uploaded, failures = scrape_and_upload_playlists()
        if failures:
            logging.error(
                f"Scheduled playlist loading finished with {len(failures)} failure(s): {failures}"
            )
        logging.info(f"Scheduled playlist loading uploaded {len(uploaded)} playlist(s)")
    except Exception as e:
        logging.error(f"Error in scheduled playlist loading: {e}")

@app.route('/spotify/auth')
def spotify_auth():
    """Initiate Spotify OAuth flow"""
    try:
        auth_url = spotify_playlist.get_auth_url()
        # get_auth_url returns None when the SPOTIPY_* credentials are unset. Passing that
        # to redirect() raised inside the except below and surfaced as a generic 500,
        # which reads as "Spotify is down" rather than "this deployment has no client id".
        if not auth_url:
            return (
                "Spotify is not configured on this server: the SPOTIPY_CLIENT_ID, "
                "SPOTIPY_CLIENT_SECRET and SPOTIPY_REDIRECT_URI environment variables "
                "must be set for the OAuth flow to start.",
                500
            )
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
        # Pass the live session, not dict(session): the token is written through the
        # cache handler into this mapping and must survive the response.
        success = spotify_playlist.handle_oauth_callback(code, session)

        if success:
            return "Successfully authenticated with Spotify! You can close this window."
        else:
            return "Failed to complete authentication", 400

    except Exception as e:
        logging.error(f"Error in Spotify callback: {e}")
        return f"Error processing callback: {str(e)}", 500

@app.route('/spotify/logout')
def spotify_logout():
    """Logout from Spotify and clear token"""
    try:
        success = spotify_playlist.clear_spotify_token(session)
        if success:
            flash("Successfully logged out from Spotify", 'success')
        else:
            flash("No active Spotify session to logout", 'info')
        return redirect(url_for('list_playlists'))
    except Exception as e:
        logging.error(f"Error in Spotify logout: {e}")
        flash(f"Error logging out: {str(e)}", 'error')
        return redirect(url_for('list_playlists'))

@app.route('/spotify/status')
def spotify_status():
    """Check Spotify authentication status"""
    try:
        is_auth = spotify_playlist.is_authenticated(session)
        return {
            'authenticated': is_auth,
            'message': 'Authenticated with Spotify' if is_auth else 'Not authenticated with Spotify'
        }
    except Exception as e:
        logging.error(f"Error checking Spotify status: {e}")
        return {
            'authenticated': False,
            'message': f'Error checking status: {str(e)}'
        }, 500


def should_start_scheduler():
    """
    Decide whether this process owns the daily cron job.

    Under uWSGI the app is loaded with --lazy-apps, so every worker imports this module
    and would otherwise start its own scheduler and run the job N times. Pin it to
    worker 1. Outside uWSGI (flask run, python app.py) there is a single process.
    """
    try:
        import uwsgi
    except ImportError:
        return True
    return uwsgi.worker_id() == 1

# NOTE: this must not run before uWSGI forks its workers. A BackgroundScheduler thread
# started pre-fork leaves its locks (and the logging module's) held forever in the
# children, which deadlocks every worker on its first request. --lazy-apps in the
# Dockerfile is what guarantees this module is imported after the fork.
scheduler = None
if should_start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=my_scheduled_job, trigger="cron", hour="23", minute="40")
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    logging.info("Started background scheduler in this process")
else:
    logging.info("Skipping background scheduler in this worker")

@app.route('/')
def index():
    """Serve the main React application"""
    return render_template('playlists.html', title="Radio to Spotify")

@app.route('/spotify')
def spotify_page():
    """Serve the React application for Spotify playlists page"""
    return render_template('playlists.html', title="Spotify Playlists")

@app.route('/load_playlist')
def load_playlist_route():
    uploaded, failures = scrape_and_upload_playlists()

    return {
        'status': 'error' if failures and not uploaded else 'success',
        'uploaded': uploaded,
        'failures': [{'source': s, 'reason': r} for s, r in failures],
    }, (500 if failures and not uploaded else 200)

@app.route('/health')
def health():
    """
    Liveness probe for Coolify/Docker.

    Deliberately unauthenticated (see PUBLIC_PATHS) and deliberately dumb: it reports
    that this uWSGI worker can accept and answer a request, nothing more. It does not
    touch S3 or Spotify, so an outage in either does not get the container restarted.
    """
    return {'status': 'ok'}, 200

@app.route('/config')
def config():
    if AWS_ACCESS_KEY_ID:
        return f"AWS_ACCESS_KEY_ID {AWS_ACCESS_KEY_ID[:4]}"
    else:
        return "AWS_ACCESS_KEY_ID is not set"

@app.route('/playlists')
def list_playlists():
    """Show the playlists page"""
    return render_template('playlists.html')

@app.route('/api/playlists')
def api_list_playlists():
    """API endpoint to get list of playlist files from S3"""
    try:
        files = playlist_upload.list_objects_in_bucket("radio-playlists")
        # Filter only CSV files and sort by name
        csv_files = sorted([f for f in files if f.endswith('.csv')])
        return {'status': 'success', 'playlists': csv_files}
    except Exception as e:
        logging.error(f"Error listing playlists: {e}")
        return {
            'status': 'error',
            'message': str(e)
        }, 500

@app.route('/create_playlist_from_file', methods=['POST'])
def create_playlist_from_file():
    """Create a Spotify playlist from a specific CSV file"""
    try:
        data = request.get_json()
        if not data or 'file_name' not in data:
            return {'status': 'error', 'message': 'No file name provided'}, 400

        file_name = data['file_name']
        # Generate a task ID
        task_id = str(uuid.uuid4())

        # Copy the session here, in the request context. Reading it inside the thread
        # raises "Working outside of request context" once the response has been sent.
        session_data = dict(session)
        if not spotify_playlist.has_cached_token(session_data):
            return SPOTIFY_AUTH_REQUIRED, 401

        # Download CSV content
        csv_content = playlist_upload.download_file_from_s3("radio-playlists", file_name)
        if not csv_content:
            return {
                'status': 'error',
                'message': f'Failed to download file: {file_name}'
            }, 400

        # Create playlist name from file name (remove .csv extension)
        playlist_name = file_name.rsplit('.', 1)[0]

        # Start playlist creation in background thread
        def run_playlist_creation():
            try:
                spotify_playlist.create_playlist_from_csv(csv_content, playlist_name, task_id, session_data)
            except Exception as e:
                logging.error(f"Error in background playlist creation: {e}")
                # Update task with error status
                if task_id in spotify_playlist.tasks:
                    spotify_playlist.tasks[task_id].update({
                        'status': 'error',
                        'message': f'Error during playlist creation: {str(e)}'
                    })
        
        # Start the background thread
        thread = threading.Thread(target=run_playlist_creation)
        thread.daemon = True  # Allow main thread to exit even if this is still running
        thread.start()
        
        return {
            'status': 'success',
            'task_id': task_id,
            'message': 'Started creating playlist'
        }
            
    except Exception as e:
        logging.error(f"Error creating playlist from file: {e}")
        return {
            'status': 'error',
            'message': f'Error creating playlist: {str(e)}'
        }, 500

@app.route('/playlist_progress/<task_id>')
def playlist_progress(task_id):
    """Get the progress of a playlist creation task"""
    task = spotify_playlist.tasks.get(task_id)
    if not task:
        return {
            'status': 'error',
            'message': 'Task not found'
        }, 404
    
    return {
        'status': task.get('status', 'processing'),
        'progress': task.get('progress', 0),
        'message': task.get('message', 'Processing...')
    }

@app.route('/playlist/<playlist_id>/tracks')
def get_playlist_tracks(playlist_id):
    """Get all tracks from a specific playlist"""
    try:
        if not spotify_playlist.has_cached_token(session):
            return SPOTIFY_AUTH_REQUIRED, 401

        tracks = spotify_playlist.get_playlist_tracks_with_session(playlist_id, session)
        
        if tracks is None:
            return {
                'status': 'error',
                'message': 'Failed to retrieve playlist tracks. Make sure you are authenticated with Spotify.'
            }, 500
        
        return {
            'status': 'success',
            'tracks': tracks,
            'total': len(tracks)
        }
        
    except Exception as e:
        logging.error(f"Error getting playlist tracks: {e}")
        return {
            'status': 'error',
            'message': f'Error retrieving playlist tracks: {str(e)}'
        }, 500

@app.route('/merge_playlists', methods=['POST'])
def merge_playlists():
    """Merge tracks from source playlist to target playlist"""
    try:
        data = request.get_json()
        if not data or 'source_playlist_id' not in data or 'target_playlist_id' not in data:
            return {'status': 'error', 'message': 'Source and target playlist IDs are required'}, 400

        source_playlist_id = data['source_playlist_id']
        target_playlist_id = data['target_playlist_id']
        
        # Generate a task ID
        task_id = str(uuid.uuid4())

        # Copy the session here, in the request context. Reading it inside the thread
        # raises "Working outside of request context" once the response has been sent.
        session_data = dict(session)
        if not spotify_playlist.has_cached_token(session_data):
            return SPOTIFY_AUTH_REQUIRED, 401

        # Start playlist merging in background thread
        def run_merge_process():
            try:
                spotify_playlist.merge_playlists(source_playlist_id, target_playlist_id, task_id, session_data)
            except Exception as e:
                logging.error(f"Error in background playlist merging: {e}")
                # Update task with error status
                if task_id in spotify_playlist.tasks:
                    spotify_playlist.tasks[task_id].update({
                        'status': 'error',
                        'message': f'Error during playlist merging: {str(e)}'
                    })
        
        # Start the background thread
        thread = threading.Thread(target=run_merge_process)
        thread.daemon = True  # Allow main thread to exit even if this is still running
        thread.start()
        
        return {
            'status': 'success',
            'task_id': task_id,
            'message': 'Started merging playlists'
        }
            
    except Exception as e:
        logging.error(f"Error merging playlists: {e}")
        return {
            'status': 'error',
            'message': f'Error merging playlists: {str(e)}'
        }, 500

@app.route('/create_playlists')
def create_playlists():
    """Create Spotify playlists from S3 CSV files"""
    try:
        if not spotify_playlist.has_cached_token(session):
            flash("Connect your Spotify account first", 'error')
            return redirect(url_for('list_playlists'))

        spotify_playlist.process_s3_playlists(dict(session))
        flash("Playlists creation process started", 'success')
        return redirect(url_for('list_playlists'))
    except Exception as e:
        logging.error(f"Error in create_playlists route: {e}")
        flash(f"Error creating playlists: {str(e)}", 'error')
        return redirect(url_for('list_playlists'))

@app.route('/spotify_playlists')
def get_spotify_playlists():
    """Get all user playlists from Spotify account as JSON response"""
    try:
        if not spotify_playlist.has_cached_token(session):
            return SPOTIFY_AUTH_REQUIRED, 401

        playlists = spotify_playlist.get_user_playlists_with_session(session)
        
        if playlists is None:
            return {
                'status': 'error',
                'message': 'Failed to retrieve playlists from Spotify. Make sure you are authenticated with Spotify.'
            }, 500
        
        return {
            'status': 'success',
            'playlists': playlists,
            'total': len(playlists)
        }
        
    except Exception as e:
        logging.error(f"Error getting Spotify playlists: {e}")
        return {
            'status': 'error',
            'message': f'Error retrieving Spotify playlists: {str(e)}'
        }, 500

@app.route('/playlists/view/<path:file_name>')
def view_playlist(file_name):
    """View contents of a specific CSV file"""
    try:
        # Download CSV content
        csv_content = playlist_upload.download_file_from_s3("radio-playlists", file_name)
        if csv_content is None:
            flash(f'Failed to download file: {file_name}', 'error')
            return redirect(url_for('list_playlists'))

        # A scrape that found no tracks writes a file holding nothing but a newline, and
        # pd.read_csv raises EmptyDataError on it. Show the page with an empty state
        # rather than bouncing the user back to the list with no explanation.
        if not csv_content.strip():
            return render_template('view_playlist.html',
                                   file_name=file_name,
                                   data=[],
                                   columns=[])

        # Parse CSV content into a DataFrame
        try:
            df = pd.read_csv(StringIO(csv_content))
        except pd.errors.EmptyDataError:
            logging.warning(f"Playlist {file_name} contains no parseable CSV data")
            return render_template('view_playlist.html',
                                   file_name=file_name,
                                   data=[],
                                   columns=[])

        # Convert DataFrame to list of dictionaries for template
        data = df.to_dict('records')
        columns = df.columns.tolist()

        return render_template('view_playlist.html',
                           file_name=file_name,
                           data=data,
                           columns=columns)
    except Exception as e:
        logging.error(f"Error viewing playlist {file_name}: {e}")
        flash(f"Error viewing playlist: {str(e)}", 'error')
        return redirect(url_for('list_playlists'))

if __name__ == '__main__':
    # In development, use flask run command instead
    # This will only be used when running python app.py directly
    app.run(host='0.0.0.0', port=8001, debug=True)
