# Radio to Spotify Playlist Creator

This application fetches radio station playlists and creates corresponding Spotify playlists automatically. It features a React/TypeScript frontend and a Python/Flask backend.

## Prerequisites

- Python 3.x
- Node.js and npm
- Docker (optional, for production deployment)

## Local Development Setup

### 1. Backend Setup

#### Create and Activate Python Virtual Environment

First, ensure you have Python 3.x installed. Then create and activate a virtual environment:

```bash
# Create virtual environment
python -m venv env

# Activate virtual environment
# On macOS/Linux:
source env/bin/activate
# On Windows:
# env\Scripts\activate

# Verify activation (should show virtual environment's Python)
which python  # On macOS/Linux
# where python  # On Windows
```

#### Install Python Dependencies

With the virtual environment activated, install the required packages:

```bash
# Upgrade pip first (recommended)
pip install --upgrade pip

# Install project dependencies (use --no-cache-dir if encountering issues)
pip install -r requirements.txt
# If you encounter issues, try:
# pip install -r requirements.txt --no-cache-dir

# Verify installations and versions
pip list | grep -E "flask|pandas|uwsgi|spotipy|boto3"

# If you encounter SSL or certificate errors, you might need to:
pip install --upgrade certifi
```

##### Troubleshooting Installation Issues

If you encounter installation problems:

1. Make sure you're in the virtual environment:
```bash
# Should show path to your project's virtual env
which python
```

2. Clear pip cache if needed:
```bash
pip cache purge
pip install -r requirements.txt --no-cache-dir
```

3. Install system dependencies if required (on Ubuntu/Debian):
```bash
sudo apt-get update
sudo apt-get install python3-dev build-essential
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

# Flask configuration
FLASK_APP=app.py
FLASK_ENV=development  # Use 'production' for production deployment
FLASK_DEBUG=1         # Enable debug mode for development
```

#### Development Environment Verification

After setup, verify your development environment:

```bash
# Check Python version and virtual environment
python --version
pip --version

# Verify Flask installation
flask --version

# Test environment variables
flask config  # Custom command to check configuration

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
flask run --debug -h 0.0.0.0 -p 8001
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
uwsgi --ini uwsgi.ini
```

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
├── static/              # Frontend directory
│   ├── package.json     # Node dependencies
│   ├── tsconfig.json    # TypeScript configuration
│   ├── vite.config.ts   # Vite configuration
│   └── ts/             # TypeScript source files
│       ├── main.tsx     # Main React entry point
│       ├── types.ts     # TypeScript type definitions
│       └── components/  # React components
├── templates/           # Flask templates
├── requirements.txt     # Python dependencies
├── .env                # Environment variables (not in git)
├── .env.template       # Template for environment variables
└── docker-compose.yml  # Docker composition for production
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

# Activate virtual environment (if not already activated)
source env/bin/activate  # On macOS/Linux
# env\Scripts\activate  # On Windows

# Start Flask development server
flask run --debug -h 0.0.0.0 -p 8001
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
