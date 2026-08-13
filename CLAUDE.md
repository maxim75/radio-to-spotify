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

- **[load_playlist.py](load_playlist.py)** — BeautifulSoup scrapers for two sources with different HTML shapes: `get_playlist_from_radiotut(station_id, day)` (day is an offset, station id is a slug like `retrofm`, Moscow time) and `get_playlist_from_raddio(station_id, date)` (numeric ids `75885, 309175, 294683`, UTC). Both return a DataFrame with `time`, `artist_name`, `song_name` — those exact column names are the contract consumed by `create_playlist_from_csv`. Rotates User-Agent via `latest-user-agents`.
- **[playlist_upload.py](playlist_upload.py)** — thin boto3 S3 wrapper. Every function swallows exceptions and returns `[]`/`None`, so callers must null-check rather than expect raises.
- **[spotify_playlist.py](spotify_playlist.py)** — all Spotify logic. Track matching is a single `sp.search(q=f"{track} artist:{artist}", limit=1)` per row; unmatched tracks are silently dropped. Writes to Spotify batch at 100 URIs (API limit).
- **[app.py](app.py)** — routes, scheduler, background threads.
- **static/ts/** — React 18 + react-router. `main.tsx` mounts routes `/` (`PlaylistsPage`, S3 CSVs) and `/spotify` (`SpotifyPlaylistsPage`, live Spotify playlists with merge). Flask serves the same `templates/playlists.html` for both `/` and `/spotify` so client-side routing survives a hard reload — a new client route needs a matching Flask route rendering that template.

### Hardcoded paths and IDs

CSVs are written to `/var/data/...` in both `load_playlist.py` and `app.py`. That path only exists inside the container (`docker-compose.yml` mounts `./data:/var/data`), so `/load_playlist` fails on a bare macOS run unless `/var/data` is created. Station IDs and the `radio-playlists` bucket name are literals scattered across `app.py`, `load_playlist.py`, and `spotify_playlist.py`.

### HTTP Basic Auth

A `@app.before_request` hook (`require_basic_auth` in `app.py`) guards **every** route — no allowlist, so static files under `/static` and the Spotify `/callback` redirect are covered too. It **fails closed**: with `BASIC_AUTH_USERNAME`/`BASIC_AUTH_PASSWORD` unset, every request gets `503` rather than running unprotected; `BASIC_AUTH_DISABLED=true` is the explicit local-dev opt-out. Comparison uses `hmac.compare_digest` on utf-8 bytes with non-short-circuiting `&` so username and password rejections take equal time.

Adding a route means it is protected automatically. Anything that must be publicly reachable (a health check, a webhook) needs an explicit early `return None` in `require_basic_auth` — and note that the `BASIC_AUTH_*` vars are read at import time, after `import spotify_playlist` has run `load_dotenv()`.

### Async work and progress

Long operations (create-from-CSV, merge) return a `task_id` immediately and run in a daemon `threading.Thread`; the frontend polls `/playlist_progress/<task_id>`. Progress lives in the module-level `spotify_playlist.tasks` dict — **in-memory, per-process, never evicted**. Under the Docker `uwsgi -p 4` config a poll can land on a worker that has no record of the task, and the APScheduler cron job (`23:40`, registered at import in `app.py`) is registered once per worker.

### Spotify auth (the fragile part)

`SessionCacheHandler` (a spotipy `CacheHandler`) stores `spotify_token_info` in the Flask session cookie instead of the `.cache*` files. Flow: `/spotify/auth` → Spotify → `/callback` → `handle_oauth_callback(code)`.

Every function that touches Spotify comes in two flavors — `get_user_playlists()` vs `get_user_playlists_with_session(session_data)` — because background threads have no request context. Routes pass `dict(session)`, a **copy**: tokens refreshed inside a thread are written to that copy and lost, and `get_auth_url`/`handle_oauth_callback` build an auth manager with an empty `{}` cache so the callback's token never reaches the user's session. This is the known-broken state the current `spotify-copilot` branch is working on (HEAD: "use session for spotify lib. Not working"). Prefer the `_with_session` variants when adding code; the non-session ones authenticate against an empty cache and will fail.

### Two Vite configs

Root `vite.config.ts` (root `./static`, `/api` proxy, `@` alias) is **not the one used** — builds run from `static/`, so `static/vite.config.ts` applies: entry `ts/main.tsx`, output `../static/dist/main.js` with a manifest plus a plugin copying `placeholder-album.png`. Edit `static/vite.config.ts` for build changes.
