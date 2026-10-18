"""Unit tests for FastAPI endpoints."""

import io
import time

from fastapi.testclient import TestClient

from fortevision.analyzer import AnalysisResult
from fortevision.api import api_app, save_job_record, set_global_analyzer
from fortevision.config import API_KEY

client = TestClient(api_app)
HEADERS = {"X-API-Key": API_KEY}


class StubAnalyzer:
    def process_video(self, video_source, job_id="", filename=None):
        return AnalysisResult(job_id=job_id, status="done", verdict="no_violation")


def _upload(name="X-1.mov"):
    with open("open/videos/X-1.mov", "rb") as f:
        files = {"file": (name, io.BytesIO(f.read()), "video/quicktime")}
    return client.post("/api/v1/analyze", headers=HEADERS, files=files)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_auth_required():
    assert client.post("/api/v1/analyze").status_code == 401
    assert client.get("/api/v1/jobs/test-id", headers={"X-API-Key": "wrong-key"}).status_code == 401


def test_analyze_and_get_job():
    set_global_analyzer(StubAnalyzer())
    post_resp = _upload()
    assert post_resp.status_code == 200
    job_id = post_resp.json()["job_id"]

    # TestClient runs background tasks before returning, so the job is finished
    job_data = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
    assert job_data == {"job_id": job_id, "status": "done", "verdict": "no_violation", "violations": []}


def test_analysis_without_model_reports_failed():
    from fortevision.analyzer import VideoAnalyzer

    set_global_analyzer(VideoAnalyzer())
    job_id = _upload().json()["job_id"]
    job_data = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
    assert job_data["status"] == "failed"
    assert "error" in job_data


def test_rejects_non_video():
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    assert client.post("/api/v1/analyze", headers=HEADERS, files=files).status_code == 400


def test_stale_job_reported_failed():
    save_job_record("stale01", {
        "job_id": "stale01", "status": "processing", "verdict": "no_violation", "violations": [],
        "_started_at": time.time() - 10_000,
    })
    data = client.get("/api/v1/jobs/stale01", headers=HEADERS).json()
    assert data["status"] == "failed"
    assert "_started_at" not in data


def test_nonexistent_job():
    assert client.get("/api/v1/jobs/nonexistent-999", headers=HEADERS).status_code == 404
