import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import logging
import pandas as pd
from io import StringIO
from playlist_upload import download_file_from_s3, list_objects_in_bucket

# Load environment variables if .env file exists
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()

# Spotify API credentials
SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
SPOTIPY_REDIRECT_URI = os.environ.get('SPOTIPY_REDIRECT_URI')
SPOTIFY_USERNAME = os.environ.get('SPOTIFY_USERNAME')

def get_auth_url():
    """
    Get the Spotify authorization URL
    """
    scope = "playlist-modify-public playlist-modify-private"
    auth_manager = SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=scope,
        username=SPOTIFY_USERNAME
    )
    return auth_manager.get_authorize_url()

def handle_oauth_callback(code):
    """
    Handle the OAuth callback and get access token
    """
    try:
        scope = "playlist-modify-public playlist-modify-private"
        auth_manager = SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope=scope,
            username=SPOTIFY_USERNAME
        )
        
        # Get the access token
        token_info = auth_manager.get_access_token(code)
        if not token_info:
            logging.error("Failed to get access token")
            return False
            
        # Save the token info for future use
        auth_manager.cache_handler.save_token_to_cache(token_info)
        logging.info("Successfully saved Spotify access token")
        return True
        
    except Exception as e:
        logging.error(f"Error handling OAuth callback: {e}")
        return False

def create_spotify_client():
    """
    Create authenticated Spotify client
    """
    scope = "playlist-modify-public playlist-modify-private"
    try:
        auth_manager = SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope=scope,
            username=SPOTIFY_USERNAME
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
        return sp
    except Exception as e:
        logging.error(f"Error creating Spotify client: {e}")
        return None

def search_track(sp, artist, track):
    """
    Search for a track on Spotify
    """
    try:
        query = f"{track} artist:{artist}"
        results = sp.search(q=query, type='track', limit=1)
        logging.info(f"Searching for track: {query}")
        logging.info(f"Search results: {results}")
        
        if results['tracks']['items']:
            return results['tracks']['items'][0]['uri']
        return None
    except Exception as e:
        logging.error(f"Error searching for track {track} by {artist}: {e}")
        return None

# Dictionary to store task progress
tasks = {}

def create_playlist_from_csv(csv_content, playlist_name, task_id):
    """
    Create a Spotify playlist from CSV content with progress tracking
    """
    try:
        # Initialize task progress
        tasks[task_id] = {
            'progress': 0,
            'message': 'Initializing...',
            'status': 'processing'
        }

        # Create Spotify client
        sp = create_spotify_client()
        if not sp:
            tasks[task_id].update({'status': 'error', 'message': 'Failed to create Spotify client'})
            return False

        # Get current user's ID
        user_id = sp.current_user()['id']
        tasks[task_id].update({'progress': 5, 'message': 'Creating playlist...'})
        
        # Create new playlist
        playlist = sp.user_playlist_create(user_id, playlist_name, public=False)
        playlist_id = playlist['id']
        
        # Load CSV content into DataFrame
        df = pd.read_csv(StringIO(csv_content))
        total_tracks = len(df)
        
        logging.info(f"Creating playlist '{playlist_name}' with {total_tracks} tracks")
        tasks[task_id].update({'progress': 10, 'message': f'Found {total_tracks} tracks to process'})

        # Collect track URIs
        track_uris = []
        for index, row in df.iterrows():
            artist = row.get('artist_name', '')
            track = row.get('song_name', '')
            logging.info(f"Processing track: {track} by {artist}")
            
            # Update progress (10-70%)
            progress = 10 + int((index / total_tracks) * 60)
            tasks[task_id].update({
                'progress': progress,
                'message': f'Searching for track: {track} by {artist}'
            })
            
            if artist and track:
                track_uri = search_track(sp, artist, track)
                if track_uri:
                    track_uris.append(track_uri)

        tasks[task_id].update({'progress': 80, 'message': 'Adding tracks to playlist...'})
        
        # Add tracks to playlist in batches
        if track_uris:
            batch_size = 100  # Spotify API limit
            for i in range(0, len(track_uris), batch_size):
                batch = track_uris[i:i + batch_size]
                sp.playlist_add_items(playlist_id, batch)
                # Update progress (80-95%)
                progress = 80 + int((i / len(track_uris)) * 15)
                tasks[task_id].update({
                    'progress': progress,
                    'message': f'Adding tracks {i+1} to {min(i+batch_size, len(track_uris))}'
                })
            
            tasks[task_id].update({
                'status': 'completed',
                'progress': 100,
                'message': f"Created playlist '{playlist_name}' with {len(track_uris)} tracks"
            })
            logging.info(f"Created playlist '{playlist_name}' with {len(track_uris)} tracks")
            return True
        else:
            tasks[task_id].update({
                'status': 'error',
                'message': f"No tracks found for playlist '{playlist_name}'"
            })
            logging.warning(f"No tracks found for playlist '{playlist_name}'")
            return False
            
    except Exception as e:
        logging.error(f"Error creating playlist: {e}")
        return False

def process_s3_playlists(bucket_name="radio-playlists"):
    """
    Process all CSV files in the S3 bucket and create Spotify playlists
    """
    try:
        # List all objects in bucket
        objects = list_objects_in_bucket(bucket_name)
        if not objects:
            logging.warning(f"No objects found in bucket {bucket_name}")
            return
        
        for obj_name in objects[:2]:
            if obj_name.endswith('.csv'):
                # Download CSV content
                csv_content = download_file_from_s3(bucket_name, obj_name)
                if csv_content:
                    # Use filename without extension as playlist name
                    playlist_name = obj_name.rsplit('.', 1)[0]
                    create_playlist_from_csv(csv_content, playlist_name)
                    
    except Exception as e:
        logging.error(f"Error processing S3 playlists: {e}")