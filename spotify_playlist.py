import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler
import os
import logging
import pandas as pd
import threading
import time
import uuid
from io import StringIO
from playlist_upload import download_file_from_s3, list_objects_in_bucket
import spotify_token_store

# Load environment variables if .env file exists
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()

# Spotify API credentials
SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
SPOTIPY_REDIRECT_URI = os.environ.get('SPOTIPY_REDIRECT_URI')

# Say so at startup rather than at first use. Missing credentials surface far from their
# cause: spotipy raises "No client_id" inside create_spotify_auth_manager, that returns
# None, /spotify/auth 500s on redirect(None), and every other Spotify route reports the
# user is "not authenticated" - which looks like an expired session, not a missing config.
_MISSING_SPOTIFY_VARS = [
    name for name, value in (
        ('SPOTIPY_CLIENT_ID', SPOTIPY_CLIENT_ID),
        ('SPOTIPY_CLIENT_SECRET', SPOTIPY_CLIENT_SECRET),
        ('SPOTIPY_REDIRECT_URI', SPOTIPY_REDIRECT_URI),
    ) if not value
]
if _MISSING_SPOTIFY_VARS:
    logging.error(
        "Spotify is not configured: %s unset. Authentication cannot complete and every "
        "Spotify route will report 'not authenticated'. In Docker these must be listed "
        "in docker-compose.yaml - .env is in .dockerignore and never reaches the container.",
        ', '.join(_MISSING_SPOTIFY_VARS)
    )

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

def has_cached_token(session_data):
    """
    Whether this session already holds a Spotify token.

    An expired token still counts: spotipy refreshes it non-interactively using the
    refresh token. What must be avoided is building a client with no token at all,
    because spotipy then falls back to its interactive console flow.
    """
    return bool((session_data or {}).get('spotify_token_info'))

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
            # Never try to open a browser or prompt on stdin: there is no console in a
            # uWSGI worker, and the prompt dies with "EOF when reading a line".
            open_browser=False,
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

def handle_oauth_callback(code, session_data):
    """
    Handle the OAuth callback and get access token.

    `session_data` must be the live Flask session, not a dict(session) copy: the token
    is written through the cache handler into that mapping, and a copy is discarded
    when the request ends, leaving the user permanently unauthenticated.
    """
    try:
        auth_manager = create_spotify_auth_manager(session_data)
        if not auth_manager:
            logging.error("Failed to create auth manager for OAuth callback")
            return False

        # Get the access token
        token_info = auth_manager.get_access_token(code, check_cache=False)
        if not token_info:
            logging.error("Failed to get access token")
            return False

        # Save the token info for future use
        auth_manager.cache_handler.save_token_to_cache(token_info)
        logging.info("Successfully saved Spotify access token to the session")

        # Also persist it server-side so the nightly job can reach Spotify with no
        # browser session. A failure here is logged and ignored: being unable to arm
        # the unattended job must not stop this user from logging in.
        if not spotify_token_store.save_token(token_info):
            logging.error(
                "Logged in, but the Spotify token could not be stored - the nightly "
                "batch will skip Spotify until this is fixed"
            )
        return True

    except Exception as e:
        logging.error(f"Error handling OAuth callback: {e}")
        return False

def create_spotify_client_with_session(session_data):
    """
    Create an authenticated Spotify client from the token held in `session_data`.

    Returns None when that session holds no token. Building a client anyway would let
    spotipy fall back to its interactive console flow, which in a uWSGI worker prints
    "Enter the URL you were redirected to:" and then raises EOFError.

    Pass the live Flask session for request-scoped work so a refreshed token is written
    back to the cookie; background threads have no request context and must pass a
    dict(session) copy, where a refresh cannot be persisted.
    """
    if not has_cached_token(session_data):
        logging.info("No Spotify token in session - not creating a client")
        return None

    try:
        auth_manager = create_spotify_auth_manager(session_data)
        if not auth_manager:
            return None

        return spotipy.Spotify(auth_manager=auth_manager)
    except Exception as e:
        logging.error(f"Error creating Spotify client with session data: {e}")
        return None

def create_spotify_client_from_store():
    """
    Build a Spotify client from the token persisted on disk, for unattended jobs.

    Returns None when nothing is stored - the scheduler thread has no console, so a
    client built without a token would print "Enter the URL you were redirected to:"
    and raise EOFError.

    The client is built over a WriteThroughTokenStore rather than a plain dict, so a
    token spotipy refreshes during the run is persisted instead of being discarded.
    """
    token_info = spotify_token_store.load_token()
    if not token_info:
        logging.warning(
            "No stored Spotify token - unattended jobs cannot reach Spotify. Connect "
            "a Spotify account once to arm them."
        )
        return None

    session_data = spotify_token_store.WriteThroughTokenStore(
        {'spotify_token_info': token_info}
    )
    return create_spotify_client_with_session(session_data)

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

# ---------------------------------------------------------------------------
# Task store
#
# Progress for the create/merge jobs, which run in daemon threads and are polled
# by /playlist_progress. Deliberately in-process: uWSGI runs a single process with
# threads (see the Dockerfile), so every request thread and every job thread shares
# this dict, and a job cannot outlive the process that owns its entry anyway.
#
# Go back to multiple uWSGI processes and this breaks silently - a poll lands on a
# worker that never saw the task and reports it as failed. That was the bug this
# replaced; the interface below exists so nothing reaches in and mutates the dict
# directly, which is what made the failure mode so easy to reintroduce.
# ---------------------------------------------------------------------------

# How long an entry survives without being written to. Long enough that no real job
# is evicted mid-flight (jobs update on every track), short enough that abandoned
# entries cannot accumulate for the life of the process.
TASK_TTL_SECONDS = 3600

_tasks = {}
_tasks_lock = threading.Lock()


def _evict_stale_locked(now):
    """Drop entries nothing has written to in TASK_TTL_SECONDS. Caller holds the lock."""
    cutoff = now - TASK_TTL_SECONDS
    stale = [key for key, task in _tasks.items() if task['_updated_at'] < cutoff]
    for key in stale:
        del _tasks[key]
    if stale:
        logging.info(f"Evicted {len(stale)} stale task(s) from the progress store")


def start_task(task_id, message='Initializing...'):
    """Register a new task as processing at 0%, sweeping stale entries as we go."""
    now = time.monotonic()
    with _tasks_lock:
        _evict_stale_locked(now)
        _tasks[task_id] = {
            'status': 'processing',
            'progress': 0,
            'message': message,
            '_updated_at': now,
        }


def update_task(task_id, **fields):
    """
    Merge `fields` into an existing task and refresh its TTL.

    A no-op for an unknown id: a job whose entry was evicted must not recreate a
    partial one, because /playlist_progress would then serve a task with no status.
    """
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.update(fields)
        task['_updated_at'] = time.monotonic()


def get_task(task_id):
    """Return a copy of the task's public fields, or None if it is not held."""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        return {key: value for key, value in task.items() if not key.startswith('_')}


def reset_tasks():
    """Empty the store. For tests; nothing in the app clears tasks wholesale."""
    with _tasks_lock:
        _tasks.clear()

def create_playlist_from_csv(csv_content, playlist_name, task_id, session_data):
    """
    Create a Spotify playlist from CSV content with progress tracking
    """
    try:
        start_task(task_id)

        sp = create_spotify_client_with_session(session_data)
        if not sp:
            update_task(
                task_id,
                status='error',
                message='Not authenticated with Spotify. Connect your account and try again.'
            )
            return False

        # Get current user's ID
        user_id = sp.current_user()['id']
        update_task(task_id, progress=5, message='Creating playlist...')

        # Create new playlist
        playlist = sp.user_playlist_create(user_id, playlist_name, public=False)
        playlist_id = playlist['id']
        
        # Load CSV content into DataFrame
        df = pd.read_csv(StringIO(csv_content))
        total_tracks = len(df)
        
        logging.info(f"Creating playlist '{playlist_name}' with {total_tracks} tracks")
        update_task(task_id, progress=10, message=f'Found {total_tracks} tracks to process')

        # Collect track URIs
        track_uris = []
        for index, row in df.iterrows():
            artist = row.get('artist_name', '')
            track = row.get('song_name', '')
            logging.info(f"Processing track: {track} by {artist}")

            # Update progress (10-70%)
            progress = 10 + int((index / total_tracks) * 60)
            update_task(
                task_id,
                progress=progress,
                message=f'Searching for track: {track} by {artist}'
            )

            if artist and track:
                track_uri = search_track(sp, artist, track)
                if track_uri:
                    track_uris.append(track_uri)

        update_task(task_id, progress=80, message='Adding tracks to playlist...')

        # Add tracks to playlist in batches
        if track_uris:
            batch_size = 100  # Spotify API limit
            for i in range(0, len(track_uris), batch_size):
                batch = track_uris[i:i + batch_size]
                sp.playlist_add_items(playlist_id, batch)
                # Update progress (80-95%)
                progress = 80 + int((i / len(track_uris)) * 15)
                update_task(
                    task_id,
                    progress=progress,
                    message=f'Adding tracks {i+1} to {min(i+batch_size, len(track_uris))}'
                )

            update_task(
                task_id,
                status='completed',
                progress=100,
                message=f"Created playlist '{playlist_name}' with {len(track_uris)} tracks"
            )
            logging.info(f"Created playlist '{playlist_name}' with {len(track_uris)} tracks")
            return True
        else:
            update_task(
                task_id,
                status='error',
                message=f"No tracks found for playlist '{playlist_name}'"
            )
            logging.warning(f"No tracks found for playlist '{playlist_name}'")
            return False

    except Exception as e:
        logging.error(f"Error creating playlist: {e}")
        update_task(task_id, status='error', message=f'Error creating playlist: {str(e)}')
        return False

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

def get_playlist_tracks(sp, playlist_id):
    """
    All tracks in a playlist, given an already-built client.

    The paging loop lives here rather than in get_playlist_tracks_with_session so
    callers that already hold a client - the upsert, and the nightly job - do not
    have to rebuild one from session data they may not have.
    """
    try:
        tracks = []
        results = sp.playlist_tracks(playlist_id)

        while results:
            for item in results['items']:
                track = item['track']
                if track:  # Handle deleted tracks
                    tracks.append({
                        'id': track['id'],
                        'name': track['name'],
                        'artist': track['artists'][0]['name'] if track['artists'] else '',
                        'uri': track['uri'],
                        'album': track['album']['name'] if track['album'] else ''
                    })

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
    sp = create_spotify_client_with_session(session_data)
    if not sp:
        logging.error("Failed to create Spotify client for getting playlist tracks")
        return None

    return get_playlist_tracks(sp, playlist_id)


def find_or_create_playlist(sp, playlist_name):
    """
    The id of the user's own playlist with this exact name, creating it if absent.

    Only playlists the user owns are considered: a followed playlist that happens to
    share the name cannot be modified, so matching it would make every add fail.
    Names are not unique, so if several match the first is used and the ambiguity is
    logged rather than guessed at silently.
    """
    user_id = sp.current_user()['id']

    matches = []
    results = sp.user_playlists(user_id)
    while results:
        for item in results['items']:
            if item['name'] == playlist_name and item['owner']['id'] == user_id:
                matches.append(item['id'])
        if results.get('next'):
            results = sp.next(results)
        else:
            break

    if matches:
        if len(matches) > 1:
            logging.warning(
                "%d playlists are named '%s'; using %s. Rename the others to remove "
                "the ambiguity.", len(matches), playlist_name, matches[0]
            )
        return matches[0]

    playlist = sp.user_playlist_create(user_id, playlist_name, public=False)
    logging.info(f"Created playlist '{playlist_name}' ({playlist['id']})")
    return playlist['id']


def upsert_tracks_into_playlist(sp, playlist_name, tracks, task_id=None):
    """
    Add `tracks` to the named playlist, skipping anything already in it.

    `tracks` is a sequence of (artist, song) pairs, already deduped by the caller.
    Running this twice over the same input adds nothing the second time: URIs are
    checked against what the playlist already holds and against each other, so two
    different track names that resolve to the same recording are added once.

    `task_id` is optional - update_task is a no-op for an unknown id, so the nightly
    job can call this without registering a task at all.

    Returns {"playlist_id", "playlist_name", "added", "skipped_existing", "unmatched"}.
    """
    update_task(task_id, progress=5, message=f"Resolving playlist '{playlist_name}'...")
    playlist_id = find_or_create_playlist(sp, playlist_name)

    update_task(task_id, progress=10, message='Reading tracks already in the playlist...')
    existing = get_playlist_tracks(sp, playlist_id) or []
    known_uris = {track['uri'] for track in existing}

    new_uris = []
    unmatched = []
    total = len(tracks)

    for index, (artist, song) in enumerate(tracks):
        # 10-70% is the search phase, matching create_playlist_from_csv's shape.
        update_task(
            task_id,
            progress=10 + int(index / total * 60) if total else 10,
            message=f'Searching for track: {song} by {artist}'
        )

        uri = search_track(sp, artist, song)
        if not uri:
            unmatched.append({'artist': artist, 'song': song})
            continue
        if uri in known_uris:
            continue

        known_uris.add(uri)
        new_uris.append(uri)

    skipped_existing = total - len(new_uris) - len(unmatched)

    update_task(
        task_id, progress=75,
        message=f'Adding {len(new_uris)} new track(s) to {playlist_name}...'
    )

    batch_size = 100  # Spotify API limit
    for i in range(0, len(new_uris), batch_size):
        batch = new_uris[i:i + batch_size]
        sp.playlist_add_items(playlist_id, batch)
        update_task(
            task_id,
            progress=75 + int(i / len(new_uris) * 20),
            message=f'Adding tracks {i + 1} to {min(i + batch_size, len(new_uris))}'
        )

    logging.info(
        "Upsert into '%s': %d added, %d already present, %d not found on Spotify",
        playlist_name, len(new_uris), skipped_existing, len(unmatched)
    )

    return {
        'playlist_id': playlist_id,
        'playlist_name': playlist_name,
        'added': len(new_uris),
        'skipped_existing': skipped_existing,
        'unmatched': unmatched,
    }


def merge_playlists(source_playlist_id, target_playlist_id, task_id, session_data=None):
    """
    Merge tracks from source playlist to target playlist, then delete source playlist
    """
    try:
        start_task(task_id, message='Starting playlist merge...')

        # Create Spotify client with session data
        sp = create_spotify_client_with_session(session_data)
        if not sp:
            update_task(task_id, status='error', message='Failed to create Spotify client')
            return False

        update_task(task_id, progress=10, message='Getting source playlist tracks...')

        # Get tracks from source playlist
        source_tracks = get_playlist_tracks_with_session(source_playlist_id, session_data)
        if not source_tracks:
            update_task(
                task_id, status='error', message='Failed to get tracks from source playlist'
            )
            return False

        update_task(
            task_id,
            progress=30,
            message=f'Found {len(source_tracks)} tracks in source playlist'
        )

        # Get tracks from target playlist to check for duplicates
        update_task(task_id, progress=40, message='Getting target playlist tracks...')
        target_tracks = get_playlist_tracks_with_session(target_playlist_id, session_data)
        if target_tracks is None:
            update_task(
                task_id, status='error', message='Failed to get tracks from target playlist'
            )
            return False

        update_task(
            task_id,
            progress=50,
            message=f'Found {len(target_tracks)} tracks in target playlist'
        )

        # Create a set of existing track URIs in target playlist for fast lookup
        target_track_uris = {track['uri'] for track in target_tracks}

        # Filter out tracks that already exist in target playlist
        new_tracks = [track for track in source_tracks if track['uri'] not in target_track_uris]

        if not new_tracks:
            update_task(
                task_id,
                status='completed',
                progress=90,
                message='No new tracks to add (all tracks already exist in target playlist)'
            )
        else:
            update_task(
                task_id,
                progress=60,
                message=f'Adding {len(new_tracks)} new tracks to target playlist...'
            )

            # Add new tracks to target playlist in batches
            new_track_uris = [track['uri'] for track in new_tracks]
            batch_size = 100  # Spotify API limit

            for i in range(0, len(new_track_uris), batch_size):
                batch = new_track_uris[i:i + batch_size]
                sp.playlist_add_items(target_playlist_id, batch)
                # Update progress (60-80%)
                progress = 60 + int((i / len(new_track_uris)) * 20)
                update_task(
                    task_id,
                    progress=progress,
                    message=f'Adding tracks {i+1} to {min(i+batch_size, len(new_track_uris))}'
                )

            update_task(
                task_id,
                progress=85,
                message=f'Successfully added {len(new_tracks)} tracks to target playlist'
            )

        # Delete the source playlist
        update_task(task_id, progress=90, message='Deleting source playlist...')

        try:
            sp.current_user_unfollow_playlist(source_playlist_id)
            logging.info(f"Successfully deleted source playlist {source_playlist_id}")
            update_task(
                task_id,
                status='completed',
                progress=100,
                message=f'Successfully merged {len(new_tracks) if new_tracks else 0} tracks and deleted source playlist'
            )
            return True

        except Exception as delete_error:
            logging.error(f"Error deleting source playlist {source_playlist_id}: {delete_error}")
            # Still consider the operation successful since tracks were merged, but warn about deletion failure
            update_task(
                task_id,
                status='completed_with_warning',
                progress=100,
                message=f'Successfully merged {len(new_tracks) if new_tracks else 0} tracks, but failed to delete source playlist: {str(delete_error)}'
            )
            return True

    except Exception as e:
        logging.error(f"Error merging playlists: {e}")
        update_task(task_id, status='error', message=f'Error merging playlists: {str(e)}')
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
        
        # Clear the server-side store too. An explicit logout that leaves the
        # unattended credential armed is the wrong surprise.
        spotify_token_store.clear_token()

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

def process_s3_playlists(session_data, bucket_name="radio-playlists"):
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
                    create_playlist_from_csv(
                        csv_content, playlist_name, str(uuid.uuid4()), session_data
                    )

    except Exception as e:
        logging.error(f"Error processing S3 playlists: {e}")
