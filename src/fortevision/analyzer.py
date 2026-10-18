"""Cash-discipline video analyzer: per-frame VLM answers -> classifier -> localized violations.

Pipeline:
1. Sample the clip at ~1 fps.
2. For each frame the VLM (Qwen3-VL) answers a fixed list of yes/no questions (features.py);
   P(Yes) is read from the logits.
3. A small classifier fitted on the open training clips scores the whole video (classifier.py).
4. For a positive verdict, the peaks of the per-type signals give timecodes and evidence frames;
   the VLM writes one factual sentence about each peak frame; each type is mapped to a verified
   regulation clause (regulations.py).
"""

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import cv2
import numpy as np
from PIL import Image

from fortevision.classifier import Classifier, load_classifier, locate_events, video_score
from fortevision.features import frame_probabilities
from fortevision.regulations import format_violation_explanation
from fortevision.video import ExtractedFrame, extract_frames, get_video_metadata, pick_evidence_frames

logger = logging.getLogger(__name__)

SCAN_FPS = 1.0
SCAN_MAX_FRAMES = 150
FRAME_MAX_DIM = 576  # Multiple of 32 (Qwen3-VL patch grid); the vision tower is the main cost per frame
EVIDENCE_FRAMES = 3

DESCRIBE_PROMPT = (
    "Опишите одним предложением по-русски, что делают люди на этом кадре с камеры над кассой банка "
    "(кассир, клиент, деньги, личные вещи, сейф). Только то, что видно, без догадок. Не описывайте лица."
)


class VLMError(RuntimeError):
    """Raised when the VLM cannot produce a usable answer."""


@dataclass
class ViolationItem:
    type: str
    timecode_sec: float
    description: str
    evidence_frames: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "timecode_sec": round(self.timecode_sec, 1),
            "description": self.description,
            "evidence_frames": self.evidence_frames,
        }


@dataclass
class AnalysisResult:
    job_id: str
    status: str  # "processing", "done", "failed"
    verdict: str  # "violation" or "no_violation"
    violations: List[ViolationItem] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "status": self.status,
            "verdict": self.verdict,
            "violations": [v.to_dict() for v in self.violations],
        }
        if self.error:
            data["error"] = self.error
        return data


def _to_vlm_image(frame: ExtractedFrame) -> Image.Image:
    h, w = frame.frame_bgr.shape[:2]
    img = frame.frame_bgr
    if max(h, w) > FRAME_MAX_DIM:
        scale = FRAME_MAX_DIM / float(max(h, w))
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


class VideoAnalyzer:
    """Orchestrates frame extraction, VLM inference, classification and regulatory enrichment."""

    def __init__(self, model=None, processor=None, device="cuda", classifier: Optional[Classifier] = None):
        self.model = model
        self.processor = processor
        self.device = device
        self.classifier = classifier if classifier is not None else load_classifier()
        # One GPU, one forward at a time; jobs run in worker threads
        self._gpu_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None and self.processor is not None and self.classifier is not None

    # --- VLM calls (overridden in tests) ---

    def _frame_probs(self, frame: ExtractedFrame) -> np.ndarray:
        with self._gpu_lock:
            return frame_probabilities(self.model, self.processor, self.device, _to_vlm_image(frame))

    def _describe(self, frame: ExtractedFrame) -> Optional[str]:
        """One factual sentence about a frame; None if the model fails (a default text is used)."""
        import torch

        try:
            messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": DESCRIBE_PROMPT}]}]
            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.processor(text=[text], images=[_to_vlm_image(frame)], return_tensors="pt").to(self.device)
            with self._gpu_lock, torch.no_grad():
                ids = self.model.generate(**inputs, max_new_tokens=80, do_sample=False)
            out = self.processor.batch_decode(ids[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
            return out.strip().replace("\n", " ") or None
        except Exception as e:  # noqa: BLE001 - description is optional
            logger.warning(f"describe failed: {e}")
            return None

    # --- Pipeline ---

    def _sample(self, video_source) -> List[ExtractedFrame]:
        fps = SCAN_FPS
        try:
            duration = get_video_metadata(video_source).duration_sec
            if duration > SCAN_MAX_FRAMES / SCAN_FPS:
                fps = SCAN_MAX_FRAMES / duration  # very long clip: sample sparser, never truncate
        except Exception:  # noqa: BLE001
            pass
        return extract_frames(video_source, target_fps=fps, max_frames=SCAN_MAX_FRAMES, adaptive=False)

    def process_video(
        self,
        video_source: Union[str, Path, bytes],
        job_id: str = "test-job",
        filename: Optional[str] = None,
    ) -> AnalysisResult:
        """Run the analysis. Raises VLMError if the model is unavailable or keeps failing."""
        frames = self._sample(video_source)
        if not frames:
            return AnalysisResult(
                job_id=job_id,
                status="failed",
                verdict="no_violation",
                error="Could not extract any valid frames from video",
            )
        if not self.ready:
            raise VLMError("VLM model or classifier is not loaded")

        try:
            series = np.array([self._frame_probs(f) for f in frames])
        except Exception as e:  # noqa: BLE001
            raise VLMError(f"VLM failed: {e}") from e
        times = np.array([f.timestamp_sec for f in frames])

        score = video_score(series, self.classifier)
        logger.info(f"Job {job_id}: video score {score:.2f} (threshold {self.classifier.threshold:.2f})")
        print(f"[ForteVision] {job_id}: score={score:.2f} threshold={self.classifier.threshold:.2f}", flush=True)
        if score < self.classifier.threshold:
            return AnalysisResult(job_id=job_id, status="done", verdict="no_violation", violations=[])

        violations: List[ViolationItem] = []
        for event in locate_events(times, series):
            tc = event["time"]
            peak = min(frames, key=lambda f: abs(f.timestamp_sec - tc))
            explanation = format_violation_explanation(event["type_key"], self._describe(peak))
            violations.append(
                ViolationItem(
                    type=explanation["type"],
                    timecode_sec=tc,
                    description=explanation["description"],
                    evidence_frames=pick_evidence_frames(frames=frames, violation_time_sec=tc, count=EVIDENCE_FRAMES),
                )
            )

        return AnalysisResult(
            job_id=job_id,
            status="done",
            verdict="violation",
            violations=violations,
        )
