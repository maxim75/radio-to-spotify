import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler
import os
import logging
import pandas as pd
import time
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

class SessionCacheHandler(CacheHandler):
    """
    Custom cache handler that stores Spotify tokens in Flask session or dictionary
    """
    def __init__(self, flask_session=None):
        # Always use the provided session_data, never default to Flask session
        # This prevents "Working outside of request context" errors in background threads
        if flask_session is not None:
            self.session = flask_session
        else:
            # If no session provided, use an empty dictionary to avoid Flask session access
            self.session = {}

    def get_cached_token(self):
        """Get token from Flask session or dictionary"""
        return self.session.get('spotify_token_info')

    def save_token_to_cache(self, token_info):
        """Save token to Flask session or dictionary"""
        self.session['spotify_token_info'] = token_info
        # Mark session as modified to ensure it gets saved (only for Flask session)
        if hasattr(self.session, 'permanent'):
            self.session.permanent = True

    def is_token_expired(self, token_info):
        """Check if token is expired"""
        now = int(time.time())
        return token_info['expires_at'] - now < 60

def create_spotify_auth_manager(session_data=None):
    """
    Create and return a configured SpotifyOAuth auth manager with session-based cache
    """
    scope = "playlist-modify-public playlist-modify-private playlist-read-private"
    try:
        # Always provide session data to avoid Flask session access in background threads
        cache_handler = SessionCacheHandler(session_data if session_data is not None else {})
        auth_manager = SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope=scope,
            cache_handler=cache_handler,
            #username=SPOTIFY_USERNAME
        )
        return auth_manager
    except Exception as e:
        logging.error(f"Error creating Spotify auth manager: {e}")
        return None

def get_auth_url():
    """
    Get the Spotify authorization URL
    """
    auth_manager = create_spotify_auth_manager()
    if not auth_manager:
        logging.error("Failed to create auth manager for auth URL")
        return None
    return auth_manager.get_authorize_url()

def handle_oauth_callback(code):
    """
    Handle the OAuth callback and get access token
    """
    try:
        auth_manager = create_spotify_auth_manager()
        if not auth_manager:
            logging.error("Failed to create auth manager for OAuth callback")
            return False
        
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
    try:
        auth_manager = create_spotify_auth_manager()
        if not auth_manager:
            logging.error("Failed to create auth manager for Spotify client")
            return None
            
        sp = spotipy.Spotify(auth_manager=auth_manager)
        return sp
    except Exception as e:
        logging.error(f"Error creating Spotify client: {e}")
        return None

def create_spotify_client_with_session(session_data):
    """
    Create authenticated Spotify client with provided session data
    """
    try:
        scope = "playlist-modify-public playlist-modify-private playlist-read-private"
        
        # Create cache handler with provided session data
        cache_handler = SessionCacheHandler(session_data)
        
        auth_manager = SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope=scope,
            cache_handler=cache_handler,
        )
        
        sp = spotipy.Spotify(auth_manager=auth_manager)
        return sp
    except Exception as e:
        logging.error(f"Error creating Spotify client with session data: {e}")
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

def create_playlist_from_csv(csv_content, playlist_name, task_id, session_data=None):
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

        # Create Spotify client with session data if provided
        if session_data:
            sp = create_spotify_client_with_session(session_data)
        else:
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
        # Update task with error status
        if task_id in tasks:
            tasks[task_id].update({
                'status': 'error',
                'message': f'Error creating playlist: {str(e)}'
            })
        return False

def get_user_playlists():
    """
    Get all playlists for the authenticated user
    """
    try:
        sp = create_spotify_client()
        if not sp:
            logging.error("Failed to create Spotify client for getting playlists")
            return None
            
        # Get current user info
        user = sp.current_user()
        user_id = user['id']
        
        # Get all user playlists
        playlists = []
        results = sp.user_playlists(user_id)
        
        while results:
            for item in results['items']:
                playlist_info = {
                    'id': item['id'],
                    'name': item['name'],
                    'description': item.get('description', ''),
                    'public': item['public'],
                    'collaborative': item['collaborative'],
                    'tracks_total': item['tracks']['total'],
                    'owner': item['owner']['display_name'],
                    'owner_id': item['owner']['id'],
                    'href': item['href'],
                    'external_url': item['external_urls']['spotify'],
                    'images': item['images'],
                    'snapshot_id': item['snapshot_id']
                }
                playlists.append(playlist_info)
            
            # Check if there are more playlists to fetch
            if results['next']:
                results = sp.next(results)
            else:
                break
                
        logging.info(f"Retrieved {len(playlists)} playlists for user {user_id}")
        return playlists
        
    except Exception as e:
        logging.error(f"Error getting user playlists: {e}")
        return None

def get_user_playlists_with_session(session_data):
    """
    Get all playlists for the authenticated user using provided session data
    """
    try:
        sp = create_spotify_client_with_session(session_data)
        if not sp:
            logging.error("Failed to create Spotify client for getting playlists")
            return None
            
        # Get current user info
        user = sp.current_user()
        user_id = user['id']
        
        # Get all user playlists
        playlists = []
        results = sp.user_playlists(user_id)
        
        while results:
            for item in results['items']:
                playlist_info = {
                    'id': item['id'],
                    'name': item['name'],
                    'description': item.get('description', ''),
                    'public': item['public'],
                    'collaborative': item['collaborative'],
                    'tracks_total': item['tracks']['total'],
                    'owner': item['owner']['display_name'],
                    'owner_id': item['owner']['id'],
                    'href': item['href'],
                    'external_url': item['external_urls']['spotify'],
                    'images': item['images'],
                    'snapshot_id': item['snapshot_id']
                }
                playlists.append(playlist_info)
            
            # Check if there are more playlists to fetch
            if results['next']:
                results = sp.next(results)
            else:
                break
                
        logging.info(f"Retrieved {len(playlists)} playlists for user {user_id}")
        return playlists
        
    except Exception as e:
        logging.error(f"Error getting user playlists: {e}")
        return None

def get_playlist_tracks(playlist_id):
    """
    Get all tracks from a specific playlist
    """
    try:
        sp = create_spotify_client()
        if not sp:
            logging.error("Failed to create Spotify client for getting playlist tracks")
            return None
            
        tracks = []
        results = sp.playlist_tracks(playlist_id)
        
        while results:
            for item in results['items']:
                track = item['track']
                if track:  # Handle deleted tracks
                    track_info = {
                        'id': track['id'],
                        'name': track['name'],
                        'artist': track['artists'][0]['name'] if track['artists'] else '',
                        'uri': track['uri'],
                        'album': track['album']['name'] if track['album'] else ''
                    }
                    tracks.append(track_info)
            
            # Check if there are more tracks to fetch
            if results['next']:
                results = sp.next(results)
            else:
                break
                
        logging.info(f"Retrieved {len(tracks)} tracks from playlist {playlist_id}")
        return tracks
        
    except Exception as e:
        logging.error(f"Error getting playlist tracks: {e}")
        return None

def get_playlist_tracks_with_session(playlist_id, session_data):
    """
    Get all tracks from a specific playlist using provided session data
    """
    try:
        sp = create_spotify_client_with_session(session_data)
        if not sp:
            logging.error("Failed to create Spotify client for getting playlist tracks")
            return None
            
        tracks = []
        results = sp.playlist_tracks(playlist_id)
        
        while results:
            for item in results['items']:
                track = item['track']
                if track:  # Handle deleted tracks
                    track_info = {
                        'id': track['id'],
                        'name': track['name'],
                        'artist': track['artists'][0]['name'] if track['artists'] else '',
                        'uri': track['uri'],
                        'album': track['album']['name'] if track['album'] else ''
                    }
                    tracks.append(track_info)
            
            # Check if there are more tracks to fetch
            if results['next']:
                results = sp.next(results)
            else:
                break
                
        logging.info(f"Retrieved {len(tracks)} tracks from playlist {playlist_id}")
        return tracks
        
    except Exception as e:
        logging.error(f"Error getting playlist tracks: {e}")
        return None

def merge_playlists(source_playlist_id, target_playlist_id, task_id, session_data=None):
    """
    Merge tracks from source playlist to target playlist, then delete source playlist
    """
    try:
        # Initialize task progress
        tasks[task_id] = {
            'progress': 0,
            'message': 'Starting playlist merge...',
            'status': 'processing'
        }

        # Create Spotify client with session data
        sp = create_spotify_client_with_session(session_data)
        if not sp:
            tasks[task_id].update({'status': 'error', 'message': 'Failed to create Spotify client'})
            return False

        tasks[task_id].update({'progress': 10, 'message': 'Getting source playlist tracks...'})
        
        # Get tracks from source playlist
        source_tracks = get_playlist_tracks_with_session(source_playlist_id, session_data)
        if not source_tracks:
            tasks[task_id].update({'status': 'error', 'message': 'Failed to get tracks from source playlist'})
            return False

        tasks[task_id].update({'progress': 30, 'message': f'Found {len(source_tracks)} tracks in source playlist'})
        
        # Get tracks from target playlist to check for duplicates
        tasks[task_id].update({'progress': 40, 'message': 'Getting target playlist tracks...'})
        target_tracks = get_playlist_tracks_with_session(target_playlist_id, session_data)
        if target_tracks is None:
            tasks[task_id].update({'status': 'error', 'message': 'Failed to get tracks from target playlist'})
            return False

        tasks[task_id].update({'progress': 50, 'message': f'Found {len(target_tracks)} tracks in target playlist'})

        # Create a set of existing track URIs in target playlist for fast lookup
        target_track_uris = {track['uri'] for track in target_tracks}
        
        # Filter out tracks that already exist in target playlist
        new_tracks = [track for track in source_tracks if track['uri'] not in target_track_uris]
        
        if not new_tracks:
            tasks[task_id].update({
                'status': 'completed',
                'progress': 90,
                'message': 'No new tracks to add (all tracks already exist in target playlist)'
            })
        else:
            tasks[task_id].update({'progress': 60, 'message': f'Adding {len(new_tracks)} new tracks to target playlist...'})
            
            # Add new tracks to target playlist in batches
            new_track_uris = [track['uri'] for track in new_tracks]
            batch_size = 100  # Spotify API limit
            
            for i in range(0, len(new_track_uris), batch_size):
                batch = new_track_uris[i:i + batch_size]
                sp.playlist_add_items(target_playlist_id, batch)
                # Update progress (60-80%)
                progress = 60 + int((i / len(new_track_uris)) * 20)
                tasks[task_id].update({
                    'progress': progress,
                    'message': f'Adding tracks {i+1} to {min(i+batch_size, len(new_track_uris))}'
                })
            
            tasks[task_id].update({'progress': 85, 'message': f'Successfully added {len(new_tracks)} tracks to target playlist'})

        # Delete the source playlist
        tasks[task_id].update({'progress': 90, 'message': 'Deleting source playlist...'})
        
        try:
            sp.current_user_unfollow_playlist(source_playlist_id)
            logging.info(f"Successfully deleted source playlist {source_playlist_id}")
            tasks[task_id].update({
                'status': 'completed',
                'progress': 100,
                'message': f'Successfully merged {len(new_tracks) if new_tracks else 0} tracks and deleted source playlist'
            })
            return True
            
        except Exception as delete_error:
            logging.error(f"Error deleting source playlist {source_playlist_id}: {delete_error}")
            # Still consider the operation successful since tracks were merged, but warn about deletion failure
            tasks[task_id].update({
                'status': 'completed_with_warning',
                'progress': 100,
                'message': f'Successfully merged {len(new_tracks) if new_tracks else 0} tracks, but failed to delete source playlist: {str(delete_error)}'
            })
            return True

    except Exception as e:
        logging.error(f"Error merging playlists: {e}")
        # Update task with error status
        if task_id in tasks:
            tasks[task_id].update({
                'status': 'error',
                'message': f'Error merging playlists: {str(e)}'
            })
        return False

def clear_spotify_token(session_data=None):
    """
    Clear Spotify token from session
    """
    try:
        # Always use provided session_data, never access Flask session directly
        # This prevents "Working outside of request context" errors in background threads
        if session_data is None:
            logging.warning("clear_spotify_token called without session_data, no action taken")
            return False
        
        if 'spotify_token_info' in session_data:
            del session_data['spotify_token_info']
            logging.info("Spotify token cleared from session")
            return True
        return False
    except Exception as e:
        logging.error(f"Error clearing Spotify token: {e}")
        return False

def is_authenticated(session_data=None):
    """
    Check if user is authenticated with Spotify
    """
    try:
        # Always provide session_data to SessionCacheHandler, even if None
        # This prevents "Working outside of request context" errors in background threads
        cache_handler = SessionCacheHandler(session_data if session_data is not None else {})
        token_info = cache_handler.get_cached_token()
        
        if not token_info:
            return False
            
        # Check if token is expired
        if cache_handler.is_token_expired(token_info):
            logging.info("Spotify token is expired")
            return False
            
        return True
    except Exception as e:
        logging.error(f"Error checking authentication status: {e}")
        return False

def refresh_token_if_needed(session_data=None):
    """
    Refresh Spotify token if needed
    """
    try:
        # Always provide session_data to SessionCacheHandler, even if None
        # This prevents "Working outside of request context" errors in background threads
        cache_handler = SessionCacheHandler(session_data if session_data is not None else {})
        token_info = cache_handler.get_cached_token()
        
        if not token_info:
            return None
            
        # Check if token needs refresh (expires within 60 seconds)
        if cache_handler.is_token_expired(token_info):
            logging.info("Refreshing Spotify token...")
            auth_manager = create_spotify_auth_manager(session_data if session_data is not None else {})
            
            # Refresh the token
            refreshed_token = auth_manager.refresh_access_token(token_info['refresh_token'])
            
            # Save the refreshed token
            cache_handler.save_token_to_cache(refreshed_token)
            logging.info("Spotify token refreshed successfully")
            return refreshed_token
            
        return token_info
    except Exception as e:
        logging.error(f"Error refreshing token: {e}")
        return None

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
