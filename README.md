# Radio to Spotify Playlist Creator

This application fetches radio station playlists and creates corresponding Spotify playlists automatically.

## Running with Docker

For production deployment:
```bash
docker-compose up --build -d
```

## Local Development Setup

### 1. Create and Activate Virtual Environment
```bash
# Create virtual environment
python -m venv env

# Activate virtual environment
# On macOS/Linux:
source env/bin/activate
# On Windows:
# env\Scripts\activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
1. Copy the environment template file:
```bash
cp .env.template .env
```

2. Edit `.env` file and fill in your credentials:
- Spotify API credentials (from https://developer.spotify.com/dashboard)
  - `SPOTIPY_CLIENT_ID`
  - `SPOTIPY_CLIENT_SECRET`
  - `SPOTIPY_REDIRECT_URI`
  - `SPOTIFY_USERNAME`
- AWS credentials
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_REGION`

### 4. Run in Development Mode
Start the application with hot reload enabled:
```bash
flask run --debug
```

By default, the application will be available at http://localhost:5000

To specify a different port or host:
```bash
flask run --debug -h 0.0.0.0 -p 8001
```

## API Endpoints

- `GET /` - Home page
- `GET /load_playlist` - Load playlists from radio stations and save to S3
- `GET /create_playlists` - Create Spotify playlists from S3 stored playlists
- `GET /config` - Check configuration status

## Development

The application will automatically reload when you make changes to the code in development mode.

Logs in development mode are stored in the `logs` directory.

## Project Structure

- `app.py` - Main Flask application
- `spotify_playlist.py` - Spotify integration logic
- `load_playlist.py` - Radio station playlist fetching logic
- `playlist_upload.py` - S3 upload/download functionality
- `requirements.txt` - Python dependencies
- `.env` - Environment variables (not in git)
- `.env.template` - Template for environment variables
- `.flaskenv` - Flask configuration
- `docker-compose.yml` - Docker composition for production deployment
