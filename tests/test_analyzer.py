"""Unit tests for analyzer, classifier and regulations."""

import numpy as np
import pytest

from fortevision.analyzer import VideoAnalyzer, VLMError
from fortevision.classifier import Classifier, locate_events
from fortevision.features import QUESTION_KEYS
from fortevision.regulations import REGULATIONS_DB, format_violation_explanation, get_regulation

SAMPLE_VIDEO = "open/videos/X-1.mov"
Q = {k: i for i, k in enumerate(QUESTION_KEYS)}


def make_classifier(threshold=0.5):
    """Flags a video when a person stands beside the cashier."""
    return Classifier(
        features=["person_beside_cashier_max"], mean=np.array([0.5]), std=np.array([0.2]),
        weights=np.array([4.0]), bias=0.0, threshold=threshold,
    )


class FakeAnalyzer(VideoAnalyzer):
    """Analyzer with GPU calls replaced by canned per-frame probabilities."""

    def __init__(self, beside_from=None, beside_to=None, classifier=None):
        super().__init__(model=object(), processor=object(), classifier=classifier or make_classifier())
        self.beside = (beside_from, beside_to)

    def _frame_probs(self, frame):
        p = np.full(len(QUESTION_KEYS), 0.02)
        lo, hi = self.beside
        if lo is not None and lo <= frame.timestamp_sec <= hi:
            p[Q["person_beside_cashier"]] = 0.97
            p[Q["standing_person"]] = 0.95
        return p

    def _describe(self, frame):
        return "Мужчина стоит за спиной кассира."


def test_regulations():
    assert len(REGULATIONS_DB) >= 7
    for key, reg in REGULATIONS_DB.items():
        assert reg.key == key
        assert reg.title_ru and reg.regulation_doc and reg.regulation_clause and reg.legal_norm
        assert reg.source_url.startswith("https://adilet.zan.kz/")
        assert reg.visual_indicators

    assert get_regulation("unauthorized_person").key == "unauthorized_person"

    exp = format_violation_explanation("cash_unattended", "На столе лежали пачки купюр.")
    assert "пачки купюр" in exp["description"]
    assert "№ 231" in exp["description"]
    assert "п. 20" in exp["description"]
    assert "adilet.zan.kz" in exp["description"]


def test_unknown_type_cites_only_document():
    exp = format_violation_explanation("other", "Кассир передал ключ клиенту")
    assert "Требуется проверка человеком" in exp["description"]


def test_locate_events_finds_peak_and_keeps_events_apart():
    times = np.arange(30, dtype=float)
    series = np.full((30, len(QUESTION_KEYS)), 0.02)
    series[10:15, Q["person_beside_cashier"]] = 0.95
    series[10:15, Q["standing_person"]] = 0.95
    series[22:26, Q["personal_items"]] = 0.9
    events = locate_events(times, series)
    assert [e["type_key"] for e in events] == ["unauthorized_person", "personal_items"] or \
        {e["type_key"] for e in events} == {"unauthorized_person", "personal_items"}
    by_type = {e["type_key"]: e["time"] for e in events}
    assert 10 <= by_type["unauthorized_person"] <= 14
    assert 22 <= by_type["personal_items"] <= 25


def test_missing_model_fails_loudly():
    with pytest.raises(VLMError):
        VideoAnalyzer(model=None, processor=None, classifier=make_classifier()).process_video(SAMPLE_VIDEO)


def test_vlm_error_fails_loudly():
    analyzer = FakeAnalyzer()
    analyzer._frame_probs = lambda frame: (_ for _ in ()).throw(RuntimeError("cuda oom"))
    with pytest.raises(VLMError):
        analyzer.process_video(SAMPLE_VIDEO)


def test_no_violation_pipeline():
    data = FakeAnalyzer().process_video(SAMPLE_VIDEO, job_id="abc").to_dict()
    assert data == {"job_id": "abc", "status": "done", "verdict": "no_violation", "violations": []}


def test_violation_pipeline_output_schema():
    data = FakeAnalyzer(beside_from=18, beside_to=24).process_video(SAMPLE_VIDEO, job_id="3f2a9c1e").to_dict()

    assert data["status"] == "done"
    assert data["verdict"] == "violation"
    v = data["violations"][0]
    assert set(v) == {"type", "timecode_sec", "description", "evidence_frames"}
    assert 18 <= v["timecode_sec"] <= 24
    assert "стоит за спиной" in v["description"]
    assert "п. 8" in v["description"]
    assert 1 <= len(v["evidence_frames"]) <= 3
    for ef in v["evidence_frames"]:
        assert set(ef) == {"timestamp_sec", "image_base64"}
        assert ef["image_base64"].startswith("/9j/")  # raw JPEG base64, no data: prefix
