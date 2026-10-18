"""Configuration settings for ForteVision."""

import os
from pathlib import Path

# Base Paths
WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent
SRC_DIR = Path(__file__).resolve().parent
DATASET_DIR = Path(os.environ.get("FORTEVISION_DATASET_DIR", WORKSPACE_DIR / "open" / "videos"))

# Hackathon / Security
# The hackathon requires X-API-Key authentication
API_KEY = os.environ.get("FORTEVISION_API_KEY", "fortevision-secret-key-2026")
HEADER_API_KEY_NAME = "X-API-Key"

# Model configuration
# Qwen3-VL-30B-A3B-Instruct: open-weights (Apache-2.0) mixture-of-experts VLM, ~60 GB in bf16 (one A100-80GB)
MODEL_ID = os.environ.get("FORTEVISION_MODEL_ID", "Qwen/Qwen3-VL-30B-A3B-Instruct")
CACHE_DIR = "/models"

# Video Processing defaults
DEFAULT_FPS_SAMPLE = 1.0  # Extract 1 frame per second for regular video scan
MAX_VIDEO_DURATION_SEC = 300  # Hackathon max processing time per video is 5 min (300s)
DEFAULT_JPEG_QUALITY = 85
EVIDENCE_MAX_FRAMES = 3  # Hackathon requirement: 1-3 evidence frames per violation
JOB_STALE_SEC = 450  # A job still "processing" after this is reported as failed

# Modal Infrastructure
MODAL_APP_NAME = "fortevision"
DEFAULT_GPU = "A100-80GB"
CONTAINER_TIMEOUT = 600  # 10 minutes max per container task
SCALEDOWN_WINDOW = 300  # Keep warm for 5 minutes between requests
