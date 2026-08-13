# Radio to Spotify Playlist Creator

This application fetches radio station playlists and creates corresponding Spotify playlists automatically. It features a React/TypeScript frontend and a Python/Flask backend.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python and all Python dependencies)
- Node.js and npm
- Docker (optional, for production deployment)

Install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Local Development Setup

### 1. Backend Setup

#### Install Python Dependencies

Python dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. uv creates
and manages the `.venv/` virtual environment for you — there is no `pip install` step and
no need to activate anything manually:

```bash
uv sync
```

This installs the exact locked versions and downloads the Python version named in
`.python-version` (3.13) if it isn't already available.

Run any command inside that environment with `uv run`:

```bash
uv run python -c "import flask, pandas, spotipy; print('ok')"
uv run flask --version
```

##### Managing Dependencies

```bash
uv add <package>            # add a dependency (updates pyproject.toml and uv.lock)
uv remove <package>         # remove a dependency
uv lock --upgrade           # re-resolve to the latest allowed versions
uv sync --group prod        # also install uWSGI (needs a C toolchain; used in Docker)
```

`uwsgi` lives in the optional `prod` dependency group because it has to be compiled from
source and is only needed by the production entrypoint, so a plain `uv sync` skips it.

If you need a `requirements.txt` for another tool, generate one from the lockfile rather
than hand-maintaining it:

```bash
uv export --format requirements-txt --no-hashes -o requirements.txt
```

#### Configure Environment Variables

1. Copy the environment template file:
```bash
cp .env.template .env
```

2. Edit `.env` file and fill in your credentials:

```ini
# Spotify API credentials (from https://developer.spotify.com/dashboard)
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIPY_REDIRECT_URI=http://localhost:8001/callback  # Default callback URL
SPOTIFY_USERNAME=your_spotify_username

# AWS credentials (from AWS IAM)
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
AWS_REGION=your_preferred_region  # e.g., us-east-1

# HTTP Basic Auth (required - see "Authentication" below)
BASIC_AUTH_USERNAME=your_username
BASIC_AUTH_PASSWORD=your_password

# Flask configuration
FLASK_APP=app.py
FLASK_ENV=development  # Use 'production' for production deployment
FLASK_DEBUG=1         # Enable debug mode for development
```

#### Development Environment Verification

After setup, verify your development environment:

```bash
# Check the Python version uv provisioned and list installed packages
uv run python --version
uv pip list

# Verify Flask installation
uv run flask --version

# Create required directories
mkdir -p logs data  # Create directories for logs and data if they don't exist
```

### 2. Frontend Setup

#### Install Node Dependencies
```bash
cd static
npm install
```

#### Start Frontend Development Server
```bash
# In the static directory
npm run dev
```

### 3. Run the Application

#### Start Backend Server
```bash
# In the project root directory
uv run flask run --debug -h 0.0.0.0 -p 8001
```

The application will be available at:
- Frontend dev server: http://localhost:5173
- Backend server: http://localhost:8001

## Production Deployment

### Using Docker

```bash
docker-compose up --build -d
```

### Manual Deployment

1. Build the frontend:
```bash
cd static
npm run build
```

2. Run the Flask application with a production server (e.g., uWSGI):
```bash
uv sync --group prod
uv run uwsgi --http 0.0.0.0:8001 --master -p 4 -w app:app
```

## Authentication

The whole application is behind HTTP Basic Auth. A `before_request` hook in `app.py`
guards **every** route, including the static bundle and the Spotify OAuth callback, so
there is nothing reachable without credentials.

Configure it with two environment variables:

| Variable | Purpose |
| --- | --- |
| `BASIC_AUTH_USERNAME` | Username required to access the app |
| `BASIC_AUTH_PASSWORD` | Password required to access the app |
| `BASIC_AUTH_REALM` | Optional; the realm shown in the browser prompt (default `Radio to Spotify`) |
| `BASIC_AUTH_DISABLED` | Optional; set to `true` to turn authentication off entirely |

**It fails closed.** If `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` are not both set,
every request is rejected with `503` and an error is logged, rather than the app quietly
running unprotected. To run locally without credentials, opt out explicitly:

```bash
BASIC_AUTH_DISABLED=true uv run flask run --debug -h 0.0.0.0 -p 8001
```

Credentials are compared with `hmac.compare_digest`, so a wrong username and a wrong
password take the same amount of time to reject.

> **Note:** Basic Auth sends the password base64-encoded, not encrypted. Serve the app
> over HTTPS (or behind a TLS-terminating reverse proxy) in production, and set
> `SESSION_COOKIE_SECURE=True` in `app.py` when you do.

## API Endpoints

- `GET /` - Home page with React frontend
- `GET /api/playlists` - List all playlists
- `GET /api/view_playlist/<filename>` - View specific playlist content
- `POST /api/create_playlist` - Create Spotify playlist from file
- `GET /api/playlist_progress/<task_id>` - Get playlist creation progress
- `GET /load_playlist` - Load playlists from radio stations and save to S3
- `GET /create_playlists` - Create Spotify playlists from S3 stored playlists
- `GET /config` - Check configuration status

## Project Structure

```
.
├── app.py                 # Main Flask application
├── spotify_playlist.py    # Spotify integration logic
├── load_playlist.py      # Radio station playlist fetching
├── playlist_upload.py    # S3 upload/download functionality
├── pyproject.toml        # Python dependencies (managed by uv)
├── uv.lock              # Pinned dependency versions
├── .python-version      # Python version uv provisions
├── static/              # Frontend directory
│   ├── package.json     # Node dependencies
│   ├── tsconfig.json    # TypeScript configuration
│   ├── vite.config.ts   # Vite configuration
│   └── ts/             # TypeScript source files
│       ├── main.tsx     # Main React entry point
│       ├── types.ts     # TypeScript type definitions
│       └── components/  # React components
├── templates/           # Flask templates
├── .env                # Environment variables (not in git)
├── .env.template       # Template for environment variables
└── docker-compose.yaml # Docker composition for production
```

## Development

### Running in Development Mode

To run the application in development mode, you'll need two terminal windows:

#### Terminal 1 - Frontend Development Server

```bash
# Navigate to frontend directory
cd static

# Start Vite development server
npm run dev
```

This will start the frontend development server with:
- Hot Module Replacement (HMR)
- Real-time TypeScript compilation
- Instant error feedback
- Available at http://localhost:5173

#### Terminal 2 - Flask Backend Server

```bash
# Ensure you're in the project root directory
cd /path/to/radio-to-spotify

# Start Flask development server (uv activates .venv for you)
uv run flask run --debug -h 0.0.0.0 -p 8001
```

This will start the Flask server with:
- Debug mode enabled (auto-reload on code changes)
- Detailed error pages
- Available at http://localhost:8001

### Development Features

#### Backend Development
- Auto-reload when Python files change
- Debug toolbar in browser (when enabled)
- Detailed error tracebacks
- Logs stored in `logs/` directory:
  - `app.log` - Application logs
  - `error.log` - Error logs
  - `access.log` - HTTP access logs

#### Frontend Development
- Hot Module Replacement (HMR)
- TypeScript type checking
- ESLint code quality checks
- Build output in `static/dist/`
- Source maps for debugging

### Debugging

#### Backend Debugging
- Set breakpoints using `breakpoint()` in Python code
- Use Flask's debug mode for interactive debugger
- Check logs in `logs/` directory
- Use `flask routes` to list all available routes

#### Frontend Debugging
- Use browser DevTools for React debugging
- TypeScript errors shown in terminal and editor
- Network requests visible in DevTools
- React Developer Tools browser extension recommended

### Common Development Tasks

#### Database Operations
```bash
# Create initial database (if using SQLite)
flask db upgrade

# Generate new migration
flask db migrate -m "description"

# Apply migrations
flask db upgrade
```

#### Testing
```bash
# Run Python tests
pytest

# Run frontend tests
cd static && npm test
```

#### Code Quality
```bash
# Python linting
flake8

# TypeScript/React linting
cd static && npm run lint
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request
