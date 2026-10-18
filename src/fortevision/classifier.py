"""Video-level violation score and event localization from per-frame VLM probabilities.

The weights (`classifier.json`) are fitted by `scripts/fit_classifier.py` on the open training
clips only. Localization is rule-based: each violation type has a signal built from the
per-frame answers, and its peak gives the timecode.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from fortevision.features import QUESTION_KEYS, pool_features, smooth

CLASSIFIER_PATH = Path(__file__).with_name("classifier.json")
EVENT_THRESHOLD = 0.5  # smoothed signal above this counts as an observed event
MAX_EVENTS = 3
MIN_EVENT_GAP_SEC = 4.0


@dataclass
class Classifier:
    features: List[str]
    mean: np.ndarray
    std: np.ndarray
    weights: np.ndarray
    bias: float
    threshold: float

    def score(self, pooled: Dict[str, float]) -> float:
        x = (np.array([pooled[f] for f in self.features]) - self.mean) / self.std
        z = float(x @ self.weights + self.bias)
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def load_classifier(path: Path = CLASSIFIER_PATH) -> Optional[Classifier]:
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    return Classifier(
        features=d["features"],
        mean=np.array(d["mean"]),
        std=np.array(d["std"]),
        weights=np.array(d["weights"]),
        bias=float(d["bias"]),
        threshold=float(d["threshold"]),
    )


# Signals: type_key -> function(sm) -> per-frame array in [0, 1]; sm maps question -> smoothed series
def _get(sm, key):
    return sm.get(key, np.zeros_like(next(iter(sm.values()))))


SIGNALS: Dict[str, Callable] = {
    "cash_unattended": lambda sm: np.minimum(_get(sm, "desk_unattended"), _get(sm, "cash_on_desk")),
    "safe_left_open": lambda sm: np.minimum(_get(sm, "desk_unattended"), _get(sm, "safe_open")),
    "cashier_absent": lambda sm: np.minimum(_get(sm, "desk_unattended"), _get(sm, "cashier_standing")),
    "unauthorized_person": lambda sm: np.minimum(_get(sm, "person_beside_cashier"), _get(sm, "standing_person")),
    "manual_counting": lambda sm: _get(sm, "cash_in_hands") * (1.0 - _get(sm, "cash_in_machine")),
    "personal_items": lambda sm: np.maximum(_get(sm, "personal_items"), _get(sm, "bag_on_desk")),
}


def locate_events(times: np.ndarray, series: np.ndarray) -> List[Dict]:
    """Events ordered by strength: [{type_key, time, strength}], at most MAX_EVENTS, spaced apart."""
    sm = {name: smooth(series[:, i]) for i, name in enumerate(QUESTION_KEYS)}
    found = []
    for key, fn in SIGNALS.items():
        sig = fn(sm)
        idx = int(np.argmax(sig))
        found.append({"type_key": key, "time": float(times[idx]), "strength": float(sig[idx])})
    found.sort(key=lambda e: -e["strength"])

    events: List[Dict] = []
    for e in found:
        if e["strength"] < EVENT_THRESHOLD and events:
            break
        if any(abs(e["time"] - o["time"]) < MIN_EVENT_GAP_SEC for o in events):
            continue
        events.append(e)
        if len(events) >= MAX_EVENTS:
            break
    return events


def video_score(series: np.ndarray, clf: Classifier) -> float:
    return clf.score(pool_features(series))
