# Media Downloader

A self-hosted web application for downloading video and audio from YouTube, Instagram, TikTok, and 1000+ other platforms.

## Features

- **Multi-platform Support**: Download from YouTube, Instagram, TikTok, Twitter/X, Facebook, Vimeo, Reddit, Twitch, and 1000+ more sites via yt-dlp
- **Quality Selection**: Choose specific resolutions (4K, 1080p, 720p, etc.) or "Best Available"
- **Audio Extraction**: Download audio-only in MP3 format
- **Cookie Support**: Upload cookies.txt to access age-restricted or private content
- **Background Downloads**: Non-blocking task queue for long downloads
- **PWA Support**: Install as a Progressive Web App on mobile/desktop
- **Per-User Isolation**: Each browser gets a unique anonymous identity — users never see each other's files or tasks (no registration required)
- **Auto Cleanup**: Downloaded files are automatically cleaned up after 30 minutes (configurable)
- **Modern UI**: Clean, responsive interface built with React and Tailwind CSS

## Quick Start

### Using Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/media-downloader.git
cd media-downloader

# Start with Docker Compose
docker compose up -d

# Access at http://localhost:8080
```

### Manual Setup

#### Backend

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --reload --port 8000
```

#### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

## Configuration

Environment variables can be set in `.env` or passed to Docker:

| Variable | Default | Description |
|----------|---------|-------------|
| `MD_FILE_RETENTION_HOURS` | 0.5 | Auto-delete files after this many hours (0.5 = 30 min) |
| `MD_CLEANUP_INTERVAL_MINUTES` | 10 | How often to run cleanup |
| `MD_MAX_CONCURRENT_DOWNLOADS` | 3 | Maximum parallel downloads |
| `MD_DEBUG` | false | Enable debug mode |

## Cookie Setup

To download age-restricted or private content:

1. Install a browser extension like "Get cookies.txt" or "EditThisCookie"
2. Log into the target platform (YouTube, Instagram, etc.)
3. Export cookies in Netscape format
4. Upload via Settings in the web UI

## API Endpoints

### Downloads

- `POST /api/downloads/extract` - Extract media info from URL
- `POST /api/downloads/start` - Start a download task
- `GET /api/downloads/tasks` - List all tasks
- `GET /api/downloads/tasks/{id}` - Get task status
- `DELETE /api/downloads/tasks/{id}` - Delete a task
- `GET /api/downloads/files` - List downloaded files
- `GET /api/downloads/files/{filename}` - Download a file
- `DELETE /api/downloads/files/{filename}` - Delete a file

### Settings

- `GET /api/settings` - Get current settings
- `POST /api/settings/cookies/upload` - Upload cookies file
- `POST /api/settings/cookies/paste` - Paste cookies content
- `DELETE /api/settings/cookies` - Delete cookies
- `POST /api/settings/cleanup` - Trigger manual cleanup

## Architecture

```
├── backend/
│   └── app/
│       ├── main.py              # FastAPI entry point
│       ├── config.py            # Configuration
│       ├── models.py            # Pydantic models
│       ├── dependencies.py      # FastAPI dependencies (client ID)
│       ├── routes/              # API endpoints
│       │   ├── downloads.py
│       │   └── settings.py
│       └── services/            # Business logic
│           ├── ytdlp_wrapper.py # yt-dlp abstraction
│           ├── task_manager.py  # Background tasks
│           └── file_manager.py  # File operations
├── frontend/
│   └── src/
│       ├── App.jsx              # Main component
│       ├── components/          # UI components
│       └── services/api.js      # API client
├── docker-compose.yml
└── Dockerfile
```

## Tech Stack

- **Backend**: Python, FastAPI, yt-dlp
- **Processing**: FFmpeg (for merging video/audio streams)
- **Frontend**: React, Tailwind CSS, Vite
- **Deployment**: Docker, Docker Compose

## License

MIT License
