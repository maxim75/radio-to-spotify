"""
Server-side persistence of the Spotify token, so the nightly job can reach Spotify
without a browser session.

This file holds a long-lived refresh token: anyone who reads it can read and modify
the user's playlists until the token is revoked. It therefore lives on the mounted
volume (already in .gitignore) rather than in the S3 bucket next to the comparatively
public CSVs, it is written owner-only, and /spotify/logout deletes it.

Like playlist_upload.py, every function swallows its exception and signals through the
return value: failing to persist the token must never break interactive login.
"""

import json
import logging
import os
import tempfile

import load_playlist


def token_path():
    """Where the token is stored. SPOTIFY_TOKEN_STORE overrides the volume default."""
    return os.environ.get("SPOTIFY_TOKEN_STORE") or os.path.join(
        load_playlist.DATA_DIR, "spotify_token.json"
    )


def save_token(token_info):
    """Write the token owner-only and atomically. Returns True when it landed."""
    path = token_path()
    directory = os.path.dirname(path) or "."
    handle = None
    tmp_path = None

    try:
        os.makedirs(directory, exist_ok=True)
        # mkstemp creates with mode 0600, so the file is never even briefly
        # world-readable; os.replace then makes a torn write impossible.
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".spotify_token.", suffix=".tmp")
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(token_info, handle)
        handle.close()
        handle = None
        os.replace(tmp_path, path)
        logging.info("Stored the Spotify token at %s", path)
        return True
    except Exception as e:
        logging.error("Could not store the Spotify token at %s: %s", path, e)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False


def load_token():
    """The stored token, or None when there is no usable one."""
    path = token_path()

    try:
        with open(path, encoding="utf-8") as handle:
            token_info = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.error("Could not read the Spotify token at %s: %s", path, e)
        return None

    # An access token with no refresh token expires within the hour and cannot be
    # renewed unattended, so it is no more use to the nightly job than nothing.
    if not isinstance(token_info, dict) or not token_info.get("refresh_token"):
        logging.error("The Spotify token at %s has no refresh_token - ignoring it", path)
        return None

    return token_info


def clear_token():
    """Delete the stored token. Returns True only if a file was actually removed."""
    path = token_path()

    try:
        os.unlink(path)
        logging.info("Cleared the stored Spotify token at %s", path)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logging.error("Could not clear the Spotify token at %s: %s", path, e)
        return False


class WriteThroughTokenStore(dict):
    """
    A session-shaped mapping that re-persists the token file when spotipy refreshes.

    The nightly job has no Flask session, so it hands SessionCacheHandler one of these
    instead. Without the write-through, a token refreshed mid-run would be written into
    a throwaway dict and lost - the same failure as passing dict(session) to a thread.

    dict.__init__ does not call __setitem__, so constructing one never writes; only a
    later assignment does.
    """

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "spotify_token_info":
            save_token(value)
