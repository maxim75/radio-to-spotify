# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Scrapes radio-station playlists from Russian radio-tracking sites, stores them as CSVs in S3, and turns them into Spotify playlists. Flask backend + React/TypeScript frontend bundled by Vite and served by Flask.

## Commands

Python deps are managed by **uv** (`pyproject.toml` + `uv.lock`, Python pinned to 3.13 by `.python-version`). There is no `requirements.txt` and no pip step; `uv sync` owns `.venv/`. The old hand-made `env/` venv is stale — ignore it.

```bash
uv sync
uv run flask run --debug -h 0.0.0.0 -p 8001
```

Use `uv add`/`uv remove` to change dependencies, never `pip install`. `uwsgi` is in the optional `prod` dependency group (it compiles from source and only the production entrypoint needs it), so a plain `uv sync` omits it — use `uv sync --group prod` to get it locally.

Frontend (all frontend work happens in `static/`):

```bash
cd static && npm install && npm run build
```

`npm run build` = `tsc && vite build` → `static/dist/main.js`. **There is no working dev server**: `static/` has no `index.html` and `package.json` has no `dev` script (`npm start` runs bare `vite` and will not serve the app). Flask's template loads the built bundle, so after any `.tsx` change you must rebuild and reload the Flask page. Type-check alone with `cd static && npx tsc --noEmit`.

Production:

```bash
docker-compose up --build -d
```

The Docker image installs deps with `uv sync --locked --group prod` and puts `/app/.venv/bin` on `PATH`, so `uwsgi` and `python` resolve directly inside the container without `uv run`.

There is no test suite, no linter config, and no database — README sections describing `pytest`, `flake8`, `npm test`, `npm run lint`, `npm run dev`, and `flask db` describe things that do not exist in this repo. The README's API endpoint list is also stale; read `app.py` for the real routes.

Credentials come from `.env` (copy `.env.template`): `SPOTIPY_CLIENT_ID/SECRET/REDIRECT_URI`, `SPOTIFY_USERNAME`, `AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY/REGION`, `BASIC_AUTH_USERNAME/PASSWORD`. `.flaskenv` sets `FLASK_APP`/debug. Only `spotify_playlist.py` calls `load_dotenv()`, so AWS vars reaching `playlist_upload.py`/`app.py` depend on that import happening first (it does, via `app.py`) — don't reorder imports in `app.py` casually.

## Architecture

Pipeline: scrape → CSV on disk → S3 bucket `radio-playlists` → Spotify playlist.

- **[load_playlist.py](load_playlist.py)** — scrapers for two sources with different shapes. `get_playlist_from_radiotut(station_id, day)` parses HTML (day is an offset, station id is a slug like `retrofm`, Moscow time). `get_playlist_from_radoxo(station_id, date)` hits Radoxo's `playlist-for-day` JSON endpoint, whose `main` key holds an HTML fragment of `li.playlist-track` rows (`.playlist-track__status[data-ts]` is a UTC epoch, `__song` and `__artist` are the text). Both return a DataFrame with `time`, `artist_name`, `song_name` — those exact column names, `PLAYLIST_COLUMNS`, are the contract consumed by `create_playlist_from_csv`. Rotates User-Agent via `latest-user-agents`.

  raddio.net became **Radoxo** and its ids did not carry over, so `RADOXO_STATION_IDS` is empty; repopulate it with `get_radoxo_station_id('https://radoxo.com/<country>/<slug>')`. Radoxo only retains about 7 days of history. The scraper raises `NoTracksFoundError` instead of returning an empty frame, and `raise_for_status()` turns a retired id into a 404 — both deliberate, because silently returning nothing is exactly what filled the bucket with 877 one-byte CSVs.
- **[playlist_upload.py](playlist_upload.py)** — thin boto3 S3 wrapper. Every function swallows exceptions and returns `[]`/`None`, so callers must null-check rather than expect raises.
- **[spotify_playlist.py](spotify_playlist.py)** — all Spotify logic. Track matching is a single `sp.search(q=f"{track} artist:{artist}", limit=1)` per row; unmatched tracks are silently dropped. Writes to Spotify batch at 100 URIs (API limit).
- **[app.py](app.py)** — routes, scheduler, background threads.
- **static/ts/** — React 18 + react-router. `main.tsx` mounts routes `/` (`PlaylistsPage`, S3 CSVs) and `/spotify` (`SpotifyPlaylistsPage`, live Spotify playlists with merge). Flask serves the same `templates/playlists.html` for both `/` and `/spotify` so client-side routing survives a hard reload — a new client route needs a matching Flask route rendering that template.

### Hardcoded paths and IDs

CSVs are written to `load_playlist.DATA_DIR`, which defaults to `/var/data` and is overridable with `PLAYLIST_DATA_DIR`. That default only exists inside the container (`docker-compose.yaml` mounts `./data:/var/data`), so set the env var for a bare macOS run. Station ids now live in one place (`load_playlist.RADOXO_STATION_IDS`), but the `radio-playlists` bucket name is still a literal scattered across `app.py`, `load_playlist.py`, and `spotify_playlist.py`.

`scrape_and_upload_playlists()` in `app.py` is the single scrape path shared by `/load_playlist` and the cron job. It **only uploads a playlist that contains tracks**, and one failing source never aborts the others — it returns `(uploaded, failures)` so both callers can report per-station outcomes.

### HTTP Basic Auth

A `@app.before_request` hook (`require_basic_auth` in `app.py`) guards **every** route — no allowlist, so static files under `/static` and the Spotify `/callback` redirect are covered too. It **fails closed**: with `BASIC_AUTH_USERNAME`/`BASIC_AUTH_PASSWORD` unset, every request gets `503` rather than running unprotected; `BASIC_AUTH_DISABLED=true` is the explicit local-dev opt-out. Comparison uses `hmac.compare_digest` on utf-8 bytes with non-short-circuiting `&` so username and password rejections take equal time.

Adding a route means it is protected automatically. Anything that must be publicly reachable (a health check, a webhook) needs an explicit early `return None` in `require_basic_auth` — and note that the `BASIC_AUTH_*` vars are read at import time, after `import spotify_playlist` has run `load_dotenv()`.

### Async work and progress

Long operations (create-from-CSV, merge) return a `task_id` immediately and run in a daemon `threading.Thread`; the frontend polls `/playlist_progress/<task_id>`. Progress lives in the module-level `spotify_playlist.tasks` dict — **in-memory, per-process, never evicted**. Under the Docker `uwsgi -p 4` config a poll can land on a worker that has no record of the task, and the APScheduler cron job (`23:40`, registered at import in `app.py`) is registered once per worker.

### Spotify auth

`SessionCacheHandler` (a spotipy `CacheHandler`) stores `spotify_token_info` in the Flask session cookie instead of the `.cache*` files. Flow: `/spotify/auth` → Spotify → `/callback` → `handle_oauth_callback(code, session)`.

Two rules govern what you pass as the session, and getting either wrong is silent:

- **Request-scoped code passes the live `session`**, never `dict(session)`. The cache handler writes the token *into* that mapping, so a copy is discarded when the response is sent — that was the bug that left `/callback` unable to authenticate anyone and made every Spotify route 500.
- **Background threads must pass a `dict(session)` copy**, captured *in the request context* before `thread.start()`. Reading `session` inside the thread raises "Working outside of request context". A token refreshed inside a thread is written to that copy and lost; the user re-authenticates.

Never build a client without checking `has_cached_token(session_data)` first. `create_spotify_client_with_session` returns `None` when there is no token precisely because spotipy would otherwise fall back to its interactive console flow, print `Enter the URL you were redirected to:` and raise `EOFError` in a uWSGI worker. Every auth manager sets `open_browser=False` for the same reason. Routes return `SPOTIFY_AUTH_REQUIRED` with 401 rather than letting that happen.

The non-session variants (`create_spotify_client`, `get_user_playlists`, `get_playlist_tracks`) were removed — they authenticated against an empty cache and could only ever fail.

### Two Vite configs

Root `vite.config.ts` (root `./static`, `/api` proxy, `@` alias) is **not the one used** — builds run from `static/`, so `static/vite.config.ts` applies: entry `ts/main.tsx`, output `../static/dist/main.js` with a manifest plus a plugin copying `placeholder-album.png`. Edit `static/vite.config.ts` for build changes.
