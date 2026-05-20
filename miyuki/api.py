"""FastAPI service for miyuki downloader."""

import logging
import os
import threading
import uuid as uuid_lib
from enum import Enum
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from miyuki.core import MiyukiService

logger = logging.getLogger("miyuki")

app = FastAPI(
    title="Miyuki API",
    description="MissAV video downloader API",
    version="0.2.0",
)


# ─── Pydantic models for API ─────────────────────────────────────────────────


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"


class DownloadRequest(BaseModel):
    movie_url: str = Field(..., description="URL of the movie page")
    quality: str | None = Field(
        default=None, description="Resolution (360, 480, 720, 1080)"
    )
    use_ffmpeg: bool = Field(default=True, description="Use ffmpeg for merging")
    download_cover: bool = Field(default=True, description="Download cover image")
    use_title_as_filename: bool = Field(
        default=False, description="Use title as filename"
    )


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    movie_url: str
    quality: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    output_path: str | None = None
    error: str | None = None


class MovieInfoResponse(BaseModel):
    url: str
    uuid: str
    title: str | None
    available_qualities: list[str]
    segment_count: int
    cover_url: str | None


class SearchResponse(BaseModel):
    keyword: str
    results: list[str]


# ─── In-memory task store ─────────────────────────────────────────────────────

_tasks: dict[str, dict[str, Any]] = {}


def _new_service() -> MiyukiService:
    """Create a fresh MiyukiService with config from environment.

    Each call creates a new instance with its own curl_cffi Session,
    which avoids thread-safety issues with shared Sessions.
    """
    return MiyukiService(
        output_dir=os.environ.get("MIYUKI_OUTPUT", "./downloads"),
        quality=os.environ.get("MIYUKI_QUALITY", "720"),
        retry=int(os.environ.get("MIYUKI_RETRY", "5")),
        delay=int(os.environ.get("MIYUKI_DELAY", "2")),
        timeout=int(os.environ.get("MIYUKI_TIMEOUT", "10")),
        proxy=os.environ.get("MIYUKI_PROXY"),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────
# Using sync `def` endpoints: FastAPI automatically runs them in a thread pool,
# and each request gets its own thread with its own MiyukiService/Session.


@app.get("/search", response_model=SearchResponse)
def search(q: str):
    """Search for movies by keyword."""
    service = _new_service()
    results = service.search(q)
    return SearchResponse(keyword=q, results=results)


@app.get("/info", response_model=MovieInfoResponse)
def get_info(url: str):
    """Get movie metadata without downloading."""
    service = _new_service()
    try:
        info = service.get_movie_info(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return MovieInfoResponse(
        url=info.url,
        uuid=info.uuid,
        title=info.title,
        available_qualities=info.available_qualities,
        segment_count=info.segment_count,
        cover_url=info.cover_url,
    )


@app.post("/tasks", response_model=TaskResponse)
def create_task(req: DownloadRequest):
    """Submit a download task. Returns immediately with a task_id.

    The download runs in a background thread. Poll GET /tasks/{task_id} for progress.
    """
    task_id = str(uuid_lib.uuid4())[:8]
    quality = req.quality or os.environ.get("MIYUKI_QUALITY", "720")

    _tasks[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.pending,
        "movie_url": req.movie_url,
        "quality": quality,
        "progress_current": 0,
        "progress_total": 0,
        "output_path": None,
        "error": None,
    }

    # Launch download in a dedicated thread (with its own Session)
    t = threading.Thread(
        target=_run_download_task,
        args=(task_id, req, quality),
        daemon=True,
    )
    t.start()

    return TaskResponse(**_tasks[task_id])


@app.get("/tasks", response_model=list[TaskResponse])
def list_tasks():
    """List all tasks."""
    return [TaskResponse(**t) for t in _tasks.values()]


@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    """Get task status and progress."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(**_tasks[task_id])


@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """Remove a task from the list (does not cancel running downloads)."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    del _tasks[task_id]
    return {"detail": "Task removed"}


# ─── Background task runner ───────────────────────────────────────────────────


def _run_download_task(task_id: str, req: DownloadRequest, quality: str):
    """Execute download in its own thread with its own MiyukiService/Session."""
    task = _tasks[task_id]
    task["status"] = TaskStatus.in_progress

    def progress_callback(current: int, total: int):
        task["progress_current"] = current
        task["progress_total"] = total

    try:
        # Each download thread creates its own service (and Session)
        service = _new_service()
        result = service.download(
            movie_url=req.movie_url,
            use_ffmpeg=req.use_ffmpeg,
            download_cover=req.download_cover,
            use_title_as_filename=req.use_title_as_filename,
            quality=quality,
            progress_callback=progress_callback,
        )
        task["status"] = TaskStatus.completed
        task["output_path"] = result.output_path
        task["progress_current"] = result.segment_downloaded
        task["progress_total"] = result.segment_total
    except Exception as e:
        task["status"] = TaskStatus.failed
        task["error"] = str(e)
        logger.error(f"Task {task_id} failed: {e}")


# ─── Server entry point ──────────────────────────────────────────────────────


def start():
    """Entry point for `miyuki-server` command."""
    logging.basicConfig(
        level=logging.INFO,
        format="Miyuki - %(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    host = os.environ.get("MIYUKI_HOST", "0.0.0.0")
    port = int(os.environ.get("MIYUKI_PORT", "8000"))
    logger.info(f"Starting Miyuki API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start()
