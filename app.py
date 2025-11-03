from flask import Flask, request, redirect, session, url_for, render_template, flash
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
from io import StringIO
import uuid
import threading

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

app = Flask(__name__, static_url_path='/static', static_folder='static/dist')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')  # Set a secret key for session management

# Configure session settings for security
app.config.update(
    SESSION_COOKIE_SECURE=False,  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=1)  # Session expires after 1 hour
)

def my_scheduled_job():
    """Scheduled job to load playlists without Flask context"""
    try:
        # Load playlist data (same logic as load_playlist_route but without Flask response)
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
        
        logging.info("Scheduled playlist loading completed successfully")
    except Exception as e:
        logging.error(f"Error in scheduled playlist loading: {e}")

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

@app.route('/spotify/logout')
def spotify_logout():
    """Logout from Spotify and clear token"""
    try:
        success = spotify_playlist.clear_spotify_token(dict(session))
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
        is_auth = spotify_playlist.is_authenticated(dict(session))
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


scheduler = BackgroundScheduler()
scheduler.add_job(func=my_scheduled_job, trigger="cron", hour="23", minute="40") 
scheduler.start()

atexit.register(lambda: scheduler.shutdown())

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
                # Copy session data to make it available in the background thread
                session_data = dict(session)
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
        tracks = spotify_playlist.get_playlist_tracks_with_session(playlist_id, dict(session))
        
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

        # Start playlist merging in background thread
        def run_merge_process():
            try:
                # Copy session data to make it available in the background thread
                session_data = dict(session)
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
        spotify_playlist.process_s3_playlists()
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
        playlists = spotify_playlist.get_user_playlists_with_session(dict(session))
        
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
        if not csv_content:
            flash(f'Failed to download file: {file_name}', 'error')
            return redirect(url_for('list_playlists'))

        # Parse CSV content into a DataFrame
        df = pd.read_csv(StringIO(csv_content))
        
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
