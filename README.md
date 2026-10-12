# Media Downloader

A self-hosted web application for downloading video and audio from YouTube, Instagram, TikTok, and 1000+ other platforms.

## Features

- **Multi-platform Support**: Download from YouTube, Instagram, TikTok, Twitter/X, Facebook, Vimeo, Reddit, Twitch, and 1000+ more sites via yt-dlp
- **Quality Selection**: Choose specific resolutions (4K, 1080p, 720p, etc.) or "Best Available"
- **Audio Extraction**: Download audio-only in MP3 format
- **Compression to a target size** (optional): fit a video into a platform limit (e.g. Discord 20 MB) or a custom size, with server-side admission control so a small server can't be overloaded
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

## Video compression

Off by default. When a user enables **Compress** on a video and picks a preset
or a custom size in MB, the video is re-encoded to fit that **file size** (a
bitrate budget, not a quality/CRF setting).

### How a request is handled

1. **Plan from metadata, before downloading.** From yt-dlp's format list:
   - `total = target × 8 / duration × (1 − overhead)`;
   - audio gets 128 → 96 → 64 → 48 → 32 kbps (≤ 20% of the budget);
   - video gets the rest.
   - The output resolution is the highest ladder step (1080/720/540/480/360/240/144,
     short side, never upscaled) where bits per pixel ≥ `MIN_BPP`. 60 fps sources
     drop to 30 fps first. If even 144p doesn't fit, the request is rejected with
     the minimum size that would work.
   - With compression on, the UI quality selector picks the **output resolution**:
     "Auto" (as above) or an exact one (`resolution` option). An exact resolution
     is never lowered silently. If it doesn't fit, the request is rejected
     (`resolution_budget`) and offers instead: the size it needs, a trim that
     fits, or Auto.
   - The smallest source that still covers the output resolution is
     downloaded, preferring H.264 (cheapest to decode).
2. **Admission.** Estimated encode time:

   ```
   duration × (out_fps × out_px × preset_factor + in_fps × in_px × decode_factor[codec])
     / throughput × two_pass_factor
   ```

   The request is accepted only if `queue_wait + estimate × SAFETY_FACTOR ≤ MAX_TOTAL_WAIT_SEC`,
   and if the queue length, per-user/per-IP limits, input size, free disk
   (`DISK_FACTOR` × input + `MIN_FREE_DISK_MB`) and RAM checks pass. An accepted
   job reserves its time and disk right away, so simultaneous requests can't
   oversubscribe the server.

   A rejected request returns HTTP 409 with the reason and the alternatives that
   *would* pass, cheapest quality loss first:
   - use the H.264 stream;
   - lower the resolution;
   - pick a faster x264 preset;
   - trim;
   - deferred queue.

   `veryfast` is the base preset. `faster` is used only when its estimate also passes.
3. **Download** into `downloads/.work/<task_id>/`. The file listing and retention cleanup ignore dot-directories.
4. **Queue → encode.** At most `MAX_CONCURRENT_JOBS` encodes run at once and the
   rest wait (deferred jobs go last). Before encoding, the real file is probed
   and re-planned.
   - ffmpeg runs two-pass libx264 with `-threads`, `nice 19`, `ionice idle`, an
     address-space limit, `oom_score_adj=1000` and a timeout
     (`estimate × TIMEOUT_MULTIPLIER`). Cancel or timeout kills the whole
     process group.
   - If the result is over the target, only pass 2 is re-run with a lower bitrate,
     up to `MAX_RETRIES` times.
   - If the source already fits (H.264/AAC mp4, no trim), it is kept as is.
5. **Result**: `<title> [<preset>].mp4` in the user's directory. The original is
   not kept. The task reports the actual size against the target and the
   resolution used.

The work directory is removed on every outcome (success, failure, cancel,
timeout, ffmpeg crash), and leftovers from a crash or restart are removed at
startup. Tasks (and the queue) are in memory and don't survive a restart.

### Throughput calibration

- At startup a 4-second synthetic 720p clip is encoded in the background. Its
  result (discounted by `CALIBRATION_DISCOUNT`) is used until real jobs exist.
- Every finished job updates the model from its measured pass times:
  `throughput` (px/s) and `two_pass_factor`. Both use a conservative aggregate
  (20th percentile / EWMA).
- CPU steal (`/proc/stat`) is measured. Samples are normalized to "no steal",
  and estimates are scaled down by the recent steal peak, so a noisy shared vCPU
  makes estimates more pessimistic.
- State is kept in `downloads/.compression/calibration.json`. If a new
  calibration differs by >30% (e.g. new hardware), old samples are dropped.
- `MD_COMPRESSION_THROUGHPUT_OVERRIDE` fixes the throughput and disables all of this.

### Settings (`MD_COMPRESSION_*`)

| Variable | Default | Meaning |
|---|---|---|
| `ENABLED` | true | Master switch |
| `PRESETS_FILE` | `app/compression_presets.json` | Platform presets: `id`, `name`, `target_size_mb`, optional `max_resolution` (short side), `audio_kbps`, `hidden` |
| `ENCODER` | libx264 | Only libx264 is implemented |
| `PRESET` / `UPGRADE_PRESET` | veryfast / faster | Base x264 preset; upgrade used only when it still passes admission (empty = off) |
| `ALLOWED_PRESETS` | ultrafast,superfast,veryfast,faster | Presets a request may ask for |
| `CPU_CORES` | 1 | Cores available for encoding |
| `MAX_CONCURRENT_JOBS` | = CPU_CORES | Simultaneous encodes |
| `THREADS` | CPU_CORES / jobs | ffmpeg threads per encode |
| `SAFETY_FACTOR` | 1.75 | Multiplier on every estimate in admission |
| `MAX_TOTAL_WAIT_SEC` | 1800 | Queue wait + encode a request may take |
| `MAX_QUEUE_LENGTH` | 5 | Accepted jobs not yet encoding (downloading or queued) |
| `MAX_JOBS_PER_USER` / `MAX_JOBS_PER_IP` | 1 / 2 | Active compression jobs per client ID / per IP (`X-Real-IP` from nginx) |
| `MAX_INPUT_DURATION_SEC` | 1200 | Hard guard, after trimming |
| `MAX_INPUT_HEIGHT` | 1080 | Max source short side |
| `MAX_INPUT_FILE_MB` | 1500 | Max source size |
| `THROUGHPUT_OVERRIDE` | – | px/s at veryfast; disables calibration |
| `CALIBRATION_DISCOUNT` | 0.8 | Synthetic clips are easier than real video |
| `TWO_PASS_FACTOR` | 1.6 | Starting (pass1+pass2)/pass2 ratio, learned from jobs |
| `PRESET_FACTORS` / `DECODE_FACTORS` | see `config.py` | JSON maps: relative encode cost per preset, decode cost per codec |
| `MIN_FREE_DISK_MB` / `DISK_FACTOR` | 2048 / 2.5 | Disk headroom |
| `MIN_FREE_RAM_MB` | 768 | RAM headroom (jobs wait up to `RAM_WAIT_SEC` for it) |
| `MIN_BPP` | 0.05 | Minimum video bits per pixel per frame |
| `CONTAINER_OVERHEAD` | 0.06 | Bitrate reserve for muxing and rate-control error |
| `AUDIO_BITRATES` / `AUDIO_SHARE` | 128…32 / 0.2 | Audio ladder and max share |
| `RESOLUTION_LADDER` | 1080…144 | Output short sides |
| `MAX_RETRIES` | 2 | Extra pass-2 runs when over target |
| `TIMEOUT_MULTIPLIER` / `MIN_TIMEOUT_SEC` | 3 / 120 | ffmpeg timeout |

Target sizes are decimal MB (1 MB = 1,000,000 bytes), the stricter reading of platform limits.

### Fitting other hardware

- **More cores:** set `CPU_CORES`. That gives one single-threaded encode per core,
  which is the most efficient use of x264 for throughput. For lower latency
  per job, set fewer `MAX_CONCURRENT_JOBS` with more `THREADS`.
- **Memory:** a 1080p job needs ~250 MB. Keep the container `mem_limit` at
  about `MAX_CONCURRENT_JOBS × 300 MB + 1 GB` for the web app and downloads.
- **Waits:** `MAX_TOTAL_WAIT_SEC` and `MAX_QUEUE_LENGTH` set how long users may
  wait. The estimate decides what actually fits, so a faster CPU automatically
  admits longer videos.
- **Tuning:** check `GET /api/downloads/compression/stats`.
  - `estimate_ratio` is actual/estimated time. Keep its p90 below `SAFETY_FACTOR`.
  - If one codec is consistently off, adjust its `DECODE_FACTORS` entry.
  - `size_ratio` close to 1.0 means `CONTAINER_OVERHEAD` could be lowered a little.
  - Per-job details: `downloads/.compression/jobs.jsonl`, and the
    `compression_job` / `compression_admission` log lines.
- **Load check on the server:** `deploy/loadtest/compression_load.sh [N] [URL] [MB]`
  starts N jobs and reports API latency, memory peak and free disk.

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
- `DELETE /api/downloads/tasks/{id}` - Delete a task (cancels it if it is a running compression task)
- `POST /api/downloads/tasks/{id}/cancel` - Cancel a compression task
- `GET /api/downloads/compression/presets` - Platform presets and limits
- `POST /api/downloads/compression/estimate` - Admission dry run: plan, time estimate, alternatives
- `GET /api/downloads/compression/stats` - Estimate-vs-actual metrics for tuning
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
