# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Media Downloader is a self-hosted web app for downloading video/audio from 1000+ platforms using yt-dlp. FastAPI backend + React frontend, deployed via Docker.

## Development Commands

### Docker (preferred)
```bash
docker compose up -d                              # Dev mode with hot reload (port 5000)
docker compose -f docker-compose.prod.yml up -d   # Production (port 8080)
```

### Backend (standalone)
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend (standalone)
```bash
cd frontend
npm install
npm run dev      # Dev server on port 3000 (proxies /api to localhost:5000)
npm run build    # Production build to dist/
```

### No test suite exists yet. No linter configuration exists.

## Architecture

### Backend (FastAPI, Python 3.11)

- **Entry point**: `backend/app/main.py` — FastAPI app with lifespan manager, APScheduler cleanup job, static file serving, SPA catch-all route
- **Config**: `backend/app/config.py` — Pydantic BaseSettings with `MD_` env prefix (MD_FILE_RETENTION_HOURS, MD_CLEANUP_INTERVAL_MINUTES, MD_MAX_CONCURRENT_DOWNLOADS, MD_DEBUG)
- **Models**: `backend/app/models.py` — Pydantic models; TaskStatus enum: PENDING → PROCESSING → DOWNLOADING → MERGING → COMPLETED/FAILED
- **Dependencies**: `backend/app/dependencies.py` — `get_client_id` FastAPI dependency (extracts/validates UUID from X-Client-ID header or client_id query param)

**Routes** (`backend/app/routes/`):
- `downloads.py` — Extract media info, start downloads, manage tasks/files, serve files/thumbnails, storage stats. All under `/api/downloads/`
- `settings.py` — Cookie management (upload/paste/delete), cleanup trigger. All under `/api/settings/`

**Services** (`backend/app/services/`):
- `ytdlp_wrapper.py` — Singleton. Wraps yt-dlp for extraction and downloading. Handles quality presets (best/4k/1440p/1080p/720p/480p/360p), FFmpeg post-processing, cookie file loading, progress hooks
- `task_manager.py` — Singleton. ThreadPoolExecutor for background downloads. In-memory task dict (UUID-keyed). Async with asyncio.Lock for thread safety. Tasks are **not persisted** across restarts. Tasks scoped by client_id
- `file_manager.py` — Singleton. Per-user file listing in `downloads/{client_id}/` subdirectories, secure path resolution (traversal protection), thumbnail management, scheduled cleanup of old files + empty user dirs, storage stats

### Frontend (React 18, Vite 5, Tailwind CSS)

- **App.jsx** — Main component holding all state. Polls tasks every 2s, files every 10s
- **Components**: SearchBar, MediaCard (quality selector + audio-only toggle), TaskList (active/completed with progress), FileList (with thumbnails), SettingsPanel (cookie management modal)
- **api.js** — Singleton ApiService class wrapping all backend endpoints with fetch. Generates per-client UUID (localStorage + cookie) and sends `X-Client-ID` header on all requests
- **PWA** enabled via vite-plugin-pwa with network-first API caching and offline support

### User Isolation

- No registration/login — anonymous users identified by client-generated UUID
- UUID stored in both localStorage (`md_client_id`) and cookie (`md_client_id`) for redundancy
- Frontend sends `X-Client-ID` header on API calls; `?client_id=` query param for direct URLs (thumbnails, file downloads)
- Backend `dependencies.py` provides `get_client_id` FastAPI dependency (validates UUID format)
- Downloads stored in per-user subdirectories: `/app/downloads/{client_id}/`
- Tasks filtered by `client_id` — users only see their own tasks and files

### Key Patterns

- All services are singletons instantiated at module level
- Downloads run in a ThreadPoolExecutor, bridged to async FastAPI via `run_in_executor`
- Frontend uses polling (not WebSockets) for real-time updates
- Vite dev server proxies `/api` requests to the backend
- Docker uses multi-stage build: Python base → Node frontend builder → dev/prod targets
- Production runs as non-root user (appuser, UID 1000)

## Environment Variables

All prefixed with `MD_`, loaded from `.env`:
- `MD_FILE_RETENTION_HOURS` (default: 0.5, i.e. 30 minutes)
- `MD_CLEANUP_INTERVAL_MINUTES` (default: 10)
- `MD_MAX_CONCURRENT_DOWNLOADS` (default: 3)
- `MD_DEBUG` (default: false)
