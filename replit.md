# A2 Downloader

A web-based YouTube video and audio downloader built with Flask and pytubefix.

## Tech Stack

- **Backend:** Python 3.12 + Flask
- **YouTube Integration:** pytubefix
- **Media Processing:** ffmpeg (system-level, available via Nix)
- **Production Server:** gunicorn

## Architecture

Single-file Flask app (`main.py`) with an embedded HTML/CSS/JS frontend.

### API Endpoints

- `GET /` - Serves the main UI
- `POST /start` - Initiates a download task, returns a task UID
- `GET /progress/<uid>` - Polls download status and percentage
- `GET /file/<uid>` - Streams the finished file to the browser

### Download Flow

1. User pastes a YouTube URL and selects quality
2. Frontend previews the video via YouTube embed
3. On download click, `/start` is called to create a background task
4. Frontend polls `/progress/<uid>` every second
5. When complete, browser redirects to `/file/<uid>` to trigger download

### Background Processing

- Uses `ThreadPoolExecutor` (6 workers) for concurrent downloads
- Temp files stored in system temp dir, cleaned up after 5 minutes

## Running

```
python main.py
```

Runs on `0.0.0.0:5000`.

## Deployment

Configured for autoscale deployment using gunicorn:
```
gunicorn --bind=0.0.0.0:5000 --reuse-port --workers=4 main:app
```
