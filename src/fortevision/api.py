"""FastAPI application implementing ForteBank Datathon API specification."""

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, MutableMapping, Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Header,
    HTTPException,
    Security,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from fortevision.analyzer import VideoAnalyzer, VLMError
from fortevision import config
from fortevision.config import API_KEY, HEADER_API_KEY_NAME, JOB_STALE_SEC

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """Content hash of the UI assets, appended as ?v= so browsers never reuse a stale cached copy."""
    h = hashlib.sha1()
    for sub in ("css/app.css", "js/player.js", "js/app.js", "js/journal.js", "journal.json"):
        f = STATIC_DIR / sub
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:10]

logger = logging.getLogger(__name__)

# Initialize FastAPI app
api_app = FastAPI(
    title="ForteVision API",
    description="ForteBank Datathon 2026 - AI Service for Cash Discipline Violation Detection",
    version="1.1.0",
)

@api_app.middleware("http")
async def revalidate_ui_assets(request, call_next):
    """Static files carry Last-Modified 1970 on Modal, so browsers would cache them 'fresh' for years; force revalidation."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Enable CORS for web UI access
api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Job store: in-memory by default; on Modal it is replaced with a modal.Dict shared by all containers
_job_store: MutableMapping[str, Dict[str, Any]] = {}

# Job runner: how an uploaded video gets analysed. Default runs in-process in a worker
# thread (so the event loop keeps serving polls); on Modal it spawns a GPU function.
JobRunner = Callable[[str, bytes, str], Awaitable[None]]
_job_runner: Optional[JobRunner] = None

# Active analyzer instance for the in-process runner
_global_analyzer: Optional[VideoAnalyzer] = None


def set_job_store(store: MutableMapping[str, Dict[str, Any]]):
    global _job_store
    _job_store = store


def set_job_runner(runner: JobRunner):
    global _job_runner
    _job_runner = runner


_warmup: Optional[Callable[[], Awaitable[None]]] = None


def set_warmup(fn: Callable[[], Awaitable[None]]):
    global _warmup
    _warmup = fn


def set_global_analyzer(analyzer: VideoAnalyzer):
    global _global_analyzer
    _global_analyzer = analyzer


def get_global_analyzer() -> VideoAnalyzer:
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = VideoAnalyzer()
    return _global_analyzer


def save_job_record(job_id: str, data: Dict[str, Any]):
    _job_store[job_id] = data


def load_job_record(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        return _job_store.get(job_id)
    except Exception as e:  # noqa: BLE001 - store backend errors must not crash polling
        logger.warning(f"Error reading job {job_id}: {e}")
        return None


def run_analysis_job(job_id: str, video_bytes: bytes, filename: Optional[str], analyzer: VideoAnalyzer):
    """Execute the analysis pipeline synchronously and store the result (any outcome)."""
    try:
        result = analyzer.process_video(video_bytes, job_id=job_id, filename=filename)
        record = result.to_dict()
        logger.info(f"Job {job_id} completed with verdict: {result.verdict}")
    except VLMError as e:
        logger.error(f"Job {job_id} failed in VLM: {e}")
        record = {"job_id": job_id, "status": "failed", "verdict": "no_violation", "violations": [], "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Job {job_id} encountered an error: {e}", exc_info=True)
        record = {"job_id": job_id, "status": "failed", "verdict": "no_violation", "violations": [], "error": str(e)}
    save_job_record(job_id, record)


async def _run_in_thread(job_id: str, video_bytes: bytes, filename: str):
    await asyncio.to_thread(run_analysis_job, job_id, video_bytes, filename, get_global_analyzer())


async def _dispatch(runner: JobRunner, job_id: str, video_bytes: bytes, filename: str):
    try:
        await runner(job_id, video_bytes, filename)
    except Exception as e:  # noqa: BLE001 - never leave a job stuck in "processing"
        logger.error(f"Job {job_id} could not be started: {e}", exc_info=True)
        save_job_record(job_id, {"job_id": job_id, "status": "failed", "verdict": "no_violation",
                                 "violations": [], "error": f"Could not start analysis: {e}"})


def verify_api_key(x_api_key: Optional[str] = Header(None, alias=HEADER_API_KEY_NAME)) -> str:
    """Validate X-API-Key header as required by hackathon spec."""
    if not x_api_key or x_api_key.strip() != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return x_api_key


# --- API Routes ---


@api_app.get("/")
async def serve_index():
    """Serve web user interface."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        html = index_file.read_text(encoding="utf-8").replace("__V__", _asset_version())
        return HTMLResponse(html)
    return {"message": "ForteVision AI service is running."}


@api_app.get("/dataset/videos/{name}")
async def dataset_video(
    name: str,
    key: Optional[str] = None,
    x_api_key: Optional[str] = Header(None, alias=HEADER_API_KEY_NAME),
):
    """Serve an open-dataset clip (Range-capable). The key comes in the X-API-Key header; `?key=` is kept for plain <video> tags."""
    supplied = x_api_key or key
    if not supplied or supplied.strip() != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing key")
    if "/" in name or "\\" in name or ".." in name or not name.lower().endswith(".mov"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid video name")
    path = config.DATASET_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    return FileResponse(path, media_type="video/quicktime")


@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "ForteVision", "version": "1.1.0"}


@api_app.get("/api/v1/warmup")
async def warmup(x_api_key: str = Security(verify_api_key)):
    """Start the GPU worker ahead of time so the first video does not pay the model load."""
    if _warmup is not None:
        await _warmup()
    return {"status": "warming"}


@api_app.post("/api/v1/analyze", status_code=status.HTTP_200_OK)
async def analyze_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    x_api_key: str = Security(verify_api_key),
):
    """Upload video in .mov format and start background analysis.

    Returns job_id immediately as per specification.
    """
    filename = file.filename or "video.mov"
    if not (filename.lower().endswith(".mov") or filename.lower().endswith(".mp4")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be in .mov format",
        )

    # Read video content
    video_bytes = await file.read()
    if not video_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded video file is empty",
        )

    # Generate 8-character unique job_id matching example in spec (e.g. '3f2a9c1e')
    job_id = uuid.uuid4().hex[:8]

    save_job_record(job_id, {
        "job_id": job_id,
        "status": "processing",
        "verdict": "no_violation",
        "violations": [],
        "_started_at": time.time(),
    })

    runner = _job_runner or _run_in_thread
    background_tasks.add_task(_dispatch, runner, job_id, video_bytes, filename)

    return {"job_id": job_id}


@api_app.get("/api/v1/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def get_job_status(
    job_id: str,
    x_api_key: str = Security(verify_api_key),
):
    """Retrieve job processing status and structured violation report."""
    job_data = load_job_record(job_id)
    if not job_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    # A worker that died mid-job would leave it "processing" forever
    started = job_data.get("_started_at")
    if job_data.get("status") == "processing" and started and time.time() - started > JOB_STALE_SEC:
        job_data = {**job_data, "status": "failed", "error": "Processing timed out"}
        save_job_record(job_id, job_data)

    return {k: v for k, v in job_data.items() if not k.startswith("_")}


api_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
