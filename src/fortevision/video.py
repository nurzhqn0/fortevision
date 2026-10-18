"""Video processing, frame extraction, and evidence generation for ForteVision."""

import base64
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image


@dataclass
class VideoMetadata:
    """Basic metadata describing a video file."""
    duration_sec: float
    fps: float
    total_frames: int
    width: int
    height: int

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class ExtractedFrame:
    """Extracted video frame with exact timestamp."""
    frame_bgr: np.ndarray
    timestamp_sec: float
    frame_index: int

    @property
    def shape(self) -> Tuple[int, int, int]:
        return self.frame_bgr.shape

    def to_pil_rgb(self) -> Image.Image:
        """Convert BGR cv2 frame to PIL Image in RGB format."""
        rgb = cv2.cvtColor(self.frame_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def to_base64_jpeg(self, quality: int = 85, max_dim: Optional[int] = 720) -> str:
        """Convert frame to base64 JPEG string."""
        return encode_frame_to_base64(self.frame_bgr, quality=quality, max_dim=max_dim)


def get_video_metadata(video_source: Union[str, Path, bytes]) -> VideoMetadata:
    """Extract metadata (duration, fps, resolution) from video path or bytes."""
    temp_path = None
    try:
        if isinstance(video_source, bytes):
            with tempfile.NamedTemporaryFile(suffix=".mov", delete=False) as f:
                f.write(video_source)
                temp_path = f.name
            video_path = temp_path
        else:
            video_path = str(video_source)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Unable to open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_sec = total_frames / fps if fps > 0 else 0.0

        cap.release()
        return VideoMetadata(
            duration_sec=round(duration_sec, 2),
            fps=round(fps, 2),
            total_frames=total_frames,
            width=width,
            height=height,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def compute_adaptive_fps(duration_sec: float) -> float:
    """Compute adaptive sampling rate to keep token count balanced.
    
    - Short clip (<= 25s): 1.5 fps -> ~20-35 frames
    - Medium clip (25s - 60s): 1.0 fps -> ~25-60 frames
    - Long clip (60s - 120s): 0.5 fps -> ~30-60 frames
    - Very long clip (> 120s): 0.33 fps -> ~40-50 frames
    """
    if duration_sec <= 25.0:
        return 1.5
    elif duration_sec <= 60.0:
        return 1.0
    elif duration_sec <= 120.0:
        return 0.5
    else:
        return 0.33


def extract_frames(
    video_source: Union[str, Path, bytes],
    target_fps: Optional[float] = None,
    max_frames: int = 36,
    adaptive: bool = True,
) -> List[ExtractedFrame]:
    """Extract frames uniformly sampled according to target or adaptive FPS."""
    temp_path = None
    try:
        if isinstance(video_source, bytes):
            with tempfile.NamedTemporaryFile(suffix=".mov", delete=False) as f:
                f.write(video_source)
                temp_path = f.name
            video_path = temp_path
        else:
            video_path = str(video_source)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Unable to open video: {video_path}")

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_sec = total_frames / native_fps if native_fps > 0 else 0.0

        if target_fps is None:
            sampling_fps = compute_adaptive_fps(duration_sec) if adaptive else 1.0
        else:
            sampling_fps = target_fps

        step = max(1, int(round(native_fps / sampling_fps)))
        # Ensure sampling spans the full video duration up to the last second
        if total_frames > 0 and (total_frames // step) > max_frames:
            step = max(1, total_frames // max_frames)

        extracted: List[ExtractedFrame] = []

        current_frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            if current_frame_idx % step == 0:
                ts = round(current_frame_idx / native_fps, 2)
                extracted.append(
                    ExtractedFrame(
                        frame_bgr=frame,
                        timestamp_sec=ts,
                        frame_index=current_frame_idx,
                    )
                )
                if len(extracted) >= max_frames:
                    break

            current_frame_idx += 1

        cap.release()
        return extracted
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def extract_dense_window(
    video_source: Union[str, Path, bytes],
    center_time_sec: float,
    window_sec: float = 6.0,
    dense_fps: float = 3.0,
    max_frames: int = 24,
) -> List[ExtractedFrame]:
    """Extract densely sampled frames around a suspicious moment for Phase 2 inspection."""
    temp_path = None
    try:
        if isinstance(video_source, bytes):
            with tempfile.NamedTemporaryFile(suffix=".mov", delete=False) as f:
                f.write(video_source)
                temp_path = f.name
            video_path = temp_path
        else:
            video_path = str(video_source)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Unable to open video: {video_path}")

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_sec = total_frames / native_fps if native_fps > 0 else 0.0

        start_sec = max(0.0, center_time_sec - (window_sec / 2.0))
        end_sec = min(duration_sec, center_time_sec + (window_sec / 2.0))

        start_frame = int(start_sec * native_fps)
        end_frame = int(end_sec * native_fps)
        step = max(1, int(round(native_fps / dense_fps)))

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        current_frame_idx = start_frame
        extracted: List[ExtractedFrame] = []

        while cap.isOpened() and current_frame_idx <= end_frame:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            if (current_frame_idx - start_frame) % step == 0:
                ts = round(current_frame_idx / native_fps, 2)
                extracted.append(
                    ExtractedFrame(
                        frame_bgr=frame,
                        timestamp_sec=ts,
                        frame_index=current_frame_idx,
                    )
                )
                if len(extracted) >= max_frames:
                    break

            current_frame_idx += 1

        cap.release()
        return extracted
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def encode_frame_to_base64(
    frame_bgr: np.ndarray,
    quality: int = 85,
    max_dim: Optional[int] = 720,
) -> str:
    """Encode OpenCV BGR frame into a JPEG base64 string."""
    h, w = frame_bgr.shape[:2]
    target_frame = frame_bgr

    if max_dim and max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        new_w = int(w * scale)
        new_h = int(h * scale)
        target_frame = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    success, buffer = cv2.imencode(".jpg", target_frame, encode_params)
    if not success:
        raise RuntimeError("Failed to encode frame as JPEG")

    return base64.b64encode(buffer).decode("utf-8")


def pick_evidence_frames(
    frames: List[ExtractedFrame],
    violation_time_sec: float,
    count: int = 2,
) -> List[dict]:
    """Select 1-3 best evidence frames around violation timestamp formatted for the API spec."""
    if not frames:
        return []

    # Sort frames by distance to target timestamp
    sorted_frames = sorted(
        frames, key=lambda f: abs(f.timestamp_sec - violation_time_sec)
    )

    # Take up to `count` distinct frames
    selected = sorted_frames[: min(count, len(sorted_frames))]
    # Sort them back chronologically
    selected.sort(key=lambda f: f.timestamp_sec)

    evidence_frames = []
    for f in selected:
        evidence_frames.append({
            "timestamp_sec": f.timestamp_sec,
            "image_base64": f.to_base64_jpeg(quality=85, max_dim=640),
        })

    return evidence_frames
