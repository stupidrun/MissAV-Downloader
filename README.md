# Miyuki

A tool for downloading videos from the MissAV website. Supports CLI and HTTP API modes.

## Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/your-repo/MissAV-Downloader.git
cd MissAV-Downloader
uv sync
```

Optional: install FFmpeg for better video quality (recommended):

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

## CLI Usage

```bash
uv run miyuki [options]
```

### Main Options (exactly one required)

| Option | Description |
|--------|-------------|
| `-urls URL [URL ...]` | Download from specific URLs |
| `-search KEYWORD` | Search by serial number and download first result |
| `-plist URL` | Download all videos from a playlist URL |
| `-auth EMAIL PASSWORD` | Login and download all favorited videos |
| `-file PATH` | Download all URLs listed in a file (one per line) |

### Additional Options

| Option | Description |
|--------|-------------|
| `-output PATH` | Output directory (default: `movies_folder_miyuki`) |
| `-quality N` | Resolution: 360, 480, 720, 1080 (default: 720) |
| `-ffmpeg` | Use FFmpeg for merging (better quality, recommended) |
| `-cover` | Download video cover image |
| `-ffcover` | Embed cover as video preview (requires FFmpeg) |
| `-title` | Use full title as output filename |
| `-proxy HOST:PORT` | HTTP(S) proxy |
| `-limit N` | Limit number of downloads (only with `-plist`) |
| `-retry N` | Retry count for segment downloads (default: 5) |
| `-delay N` | Delay in seconds before retry (default: 2) |
| `-timeout N` | Request timeout in seconds (default: 10) |
| `-noban` | Hide the startup banner |

### Examples

```bash
# Search and download
uv run miyuki -search sw-950 -ffmpeg

# Specify quality and output directory
uv run miyuki -search sw-950 -quality 1080 -output ~/Videos -ffmpeg

# Download from URL with proxy
uv run miyuki -urls https://missav.live/sw-950 -proxy localhost:7890 -ffmpeg -cover

# Batch download from playlist (limit 10)
uv run miyuki -plist "https://missav.live/search/JULIA?filters=uncensored-leak&sort=saved" -limit 10 -ffmpeg

# Download from file
uv run miyuki -file urls.txt -ffmpeg -title
```

### Environment Variables

All options support environment variable fallback (CLI args take priority):

| Variable | Default | Description |
|----------|---------|-------------|
| `MIYUKI_QUALITY` | `720` | Default resolution |
| `MIYUKI_OUTPUT` | `movies_folder_miyuki` | Output directory |
| `MIYUKI_RETRY` | `5` | Retry count |
| `MIYUKI_DELAY` | `2` | Retry delay (seconds) |
| `MIYUKI_TIMEOUT` | `10` | Request timeout (seconds) |

## API Server

Start the HTTP API server:

```bash
uv run miyuki-server
```

Server listens on `http://0.0.0.0:8000` by default. Interactive docs at `http://localhost:8000/docs`.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/search?q=keyword` | Search for videos |
| GET | `/info?url=...` | Get video metadata (without downloading) |
| POST | `/tasks` | Submit a download task |
| GET | `/tasks` | List all tasks |
| GET | `/tasks/{task_id}` | Get task status and progress |
| DELETE | `/tasks/{task_id}` | Remove a task record |

### POST /tasks — Request Body

```json
{
  "movie_url": "https://missav.live/sw-950",
  "quality": "720",
  "use_ffmpeg": true,
  "download_cover": true,
  "use_title_as_filename": false,
  "webhook_url": "https://your-server.com/hooks/miyuki"
}
```

All fields except `movie_url` are optional.

### POST /tasks — Response

```json
{
  "task_id": "a1b2c3d4",
  "status": "pending",
  "movie_url": "https://missav.live/sw-950",
  "quality": "720",
  "progress_current": 0,
  "progress_total": 0,
  "output_path": null,
  "error": null
}
```

Poll `GET /tasks/{task_id}` to track progress. `progress_current` / `progress_total` updates in real-time during download.

### Webhook Notification

If `webhook_url` is provided in the download request, a POST will be sent to that URL when the task completes or fails:

```json
{
  "event": "task.completed",
  "task_id": "a1b2c3d4",
  "movie_url": "https://missav.live/sw-950",
  "title": "SW-950 ...",
  "status": "completed",
  "quality": "720",
  "output_path": "/downloads/sw-950_720p.mp4",
  "segment_total": 3074,
  "segment_downloaded": 3074,
  "error": null,
  "timestamp": "2025-05-19T23:44:04+00:00"
}
```

The `event` field is either `task.completed` or `task.failed`.

### Server Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MIYUKI_HOST` | `0.0.0.0` | Listen address |
| `MIYUKI_PORT` | `8000` | Listen port |
| `MIYUKI_OUTPUT` | `./downloads` | Download output directory |
| `MIYUKI_QUALITY` | `720` | Default resolution |
| `MIYUKI_PROXY` | — | HTTP proxy (e.g. `localhost:7890`) |
| `MIYUKI_RETRY` | `5` | Retry count |
| `MIYUKI_DELAY` | `2` | Retry delay (seconds) |
| `MIYUKI_TIMEOUT` | `10` | Request timeout (seconds) |

## Docker Deployment

### Quick Start

```bash
docker compose up -d
```

This builds the image (includes FFmpeg), starts the server on port 8000, and mounts `./downloads` for output.

### docker-compose.yml

```yaml
services:
  miyuki:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./downloads:/downloads
    environment:
      - MIYUKI_OUTPUT=/downloads
      - MIYUKI_QUALITY=720
      # - MIYUKI_PROXY=host.docker.internal:7890
    restart: unless-stopped
```

### Using Proxy in Docker

To route traffic through a proxy running on the host machine:

```yaml
environment:
  - MIYUKI_PROXY=host.docker.internal:7890
```

### Health Check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Project Structure

```
miyuki/
├── __init__.py    — Public API exports
├── client.py      — HTTP client (TLS fingerprint impersonation, retry)
├── models.py      — Data models (MovieInfo, DownloadResult)
├── core.py        — Core service (MiyukiService class)
├── cli.py         — CLI entry point
└── api.py         — FastAPI server
```

### Using as a Library

```python
from miyuki import MiyukiService

service = MiyukiService(output_dir="./my_videos", quality="1080")

# Search
urls = service.search("sw-950")

# Get info without downloading
info = service.get_movie_info(urls[0])
print(info.title, info.available_qualities)

# Download
result = service.download(urls[0], use_ffmpeg=True)
print(result.output_path)
```

## How It Works

1. Fetches the video page HTML from MissAV
2. Extracts the video UUID from obfuscated JavaScript
3. Retrieves the HLS playlist from the CDN (`surrit.com`)
4. Selects the requested resolution
5. Downloads all video segments (`.jpeg` files — actually MPEG-TS segments disguised with image extensions)
6. Merges segments into a single `.mp4` file (via FFmpeg or binary concatenation)

## Notes

- Uses `curl_cffi` for TLS fingerprint impersonation to bypass Cloudflare protection
- A `Referer` header is required for CDN requests
- Downloaded URLs are recorded in `downloaded_urls_miyuki.txt` to avoid re-downloading. Delete this file or the relevant line to re-download
- Without `-ffmpeg`, videos are merged by binary concatenation (works but seeking may be imprecise in some players)

## License

[MIT License](LICENSE)
