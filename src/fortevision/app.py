"""Modal Application definition for ForteVision."""

import os
from pathlib import Path
import modal

try:
    from fortevision.config import MODAL_APP_NAME
except ImportError:
    from config import MODAL_APP_NAME

app = modal.App(MODAL_APP_NAME)

# Persistent volume for caching model weights across cold starts
model_volume = modal.Volume.from_name("fortevision-models", create_if_missing=True)

# Container image with CUDA, PyTorch, Transformers, OpenCV, ffmpeg, FastAPI
app_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0", "git")
    .pip_install(
        "torch>=2.4.0",
        "torchvision",
        "transformers>=4.49.0",
        "accelerate>=0.34.0",
        "opencv-python-headless>=4.10.0",
        "pillow>=10.4.0",
        "numpy>=1.26.0",
        "fastapi[standard]>=0.115.0",
        "pydantic>=2.8.0",
        "huggingface_hub>=0.28.0",
        "python-multipart>=0.0.12",
    )
    .env({"FORTEVISION_DATASET_DIR": "/root/dataset/videos"})
    .add_local_python_source("fortevision")
    .add_local_dir("open/videos", remote_path="/root/dataset/videos")
    .add_local_dir("src/fortevision/static", remote_path="/root/fortevision/static")
    .add_local_file("src/fortevision/classifier.json", remote_path="/root/fortevision/classifier.json")
)


@app.function(
    image=app_image,
    gpu="A100-80GB",
    volumes={"/models": model_volume},
    timeout=300,
)
def check_environment():
    """Verify GPU availability, CUDA version, PyTorch and environment inside Modal."""
    import torch
    import transformers
    import cv2
    import os

    cuda_available = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_available else "No GPU"
    vram_gb = (
        torch.cuda.get_device_properties(0).total_memory / (1024**3)
        if cuda_available
        else 0
    )

    models_dir_contents = os.listdir("/models") if os.path.exists("/models") else []

    info = {
        "status": "ready",
        "cuda_available": cuda_available,
        "device_name": device_name,
        "vram_gb": round(vram_gb, 2),
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
        "opencv_version": cv2.__version__,
        "models_volume_contents": models_dir_contents,
    }
    print("[ForteVision Environment Check]")
    for k, v in info.items():
        print(f"  {k}: {v}")
    return info


JOBS_DICT_NAME = "fortevision-jobs"


def _jobs_store():
    """Job records shared by the web and GPU containers."""
    return modal.Dict.from_name(JOBS_DICT_NAME, create_if_missing=True)


JOBS_DICT_NAME = "fortevision-jobs"


def _jobs_store():
    """Job records shared by the web and GPU containers."""
    return modal.Dict.from_name(JOBS_DICT_NAME, create_if_missing=True)


@app.cls(
    image=app_image,
    gpu="A100-80GB",
    volumes={"/models": model_volume},
    timeout=420,
    # A cold start loads ~60 GB of weights; stay warm between jury requests.
    # Set min_containers=1 here to guarantee a warm worker.
    scaledown_window=1800,
    max_containers=3,
)
@modal.concurrent(max_inputs=2)
class ForteVisionAnalyzer:
    """GPU worker: loads the VLM once per container and analyses spawned jobs."""

    @modal.enter()
    def load_model(self):
        import os
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        from fortevision.analyzer import VideoAnalyzer
        from fortevision.api import set_job_store
        from fortevision.config import MODEL_ID

        cache_dir = "/models/huggingface"
        os.makedirs(cache_dir, exist_ok=True)

        def load(local_only: bool):
            model = AutoModelForImageTextToText.from_pretrained(
                MODEL_ID,
                dtype=torch.bfloat16,
                device_map="auto",
                cache_dir=cache_dir,
                local_files_only=local_only,
            )
            processor = AutoProcessor.from_pretrained(MODEL_ID, cache_dir=cache_dir, local_files_only=local_only)
            return model, processor

        # Weights are cached on the volume: no internet needed once downloaded
        try:
            model, processor = load(local_only=True)
            print("[ForteVision] Loaded model weights from local cache (offline).")
        except Exception as e:  # noqa: BLE001 - first run: download once, then cache
            print(f"[ForteVision] Local cache miss ({e}); downloading weights once...")
            model, processor = load(local_only=False)
            model_volume.commit()

        self.analyzer = VideoAnalyzer(model=model, processor=processor, device="cuda")
        set_job_store(_jobs_store())
        print("[ForteVision] VLM loaded into VRAM successfully!")

    @modal.method()
    def analyze(self, job_id: str, video_bytes: bytes, filename: str):
        from fortevision.api import run_analysis_job

        run_analysis_job(job_id, video_bytes, filename, self.analyzer)

    @modal.method()
    def warmup(self):
        return "ready"


@app.cls(
    image=app_image,
    timeout=600,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=50)
class ForteVisionService:
    """Public web UI + REST API (CPU). Analysis runs in ForteVisionAnalyzer on GPU."""

    @modal.asgi_app()
    def web(self):
        """Serve FastAPI app with both REST API and Web Dashboard."""
        from fortevision.api import api_app, set_job_runner, set_job_store, set_warmup

        set_job_store(_jobs_store())

        async def spawn_job(job_id: str, video_bytes: bytes, filename: str):
            await ForteVisionAnalyzer().analyze.spawn.aio(job_id, video_bytes, filename)

        async def warmup():
            await ForteVisionAnalyzer().warmup.spawn.aio()

        set_job_runner(spawn_job)
        set_warmup(warmup)
        return api_app
