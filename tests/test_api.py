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


def test_dataset_video_requires_key():
    assert client.get("/dataset/videos/X-1.mov").status_code == 401
    assert client.get("/dataset/videos/X-1.mov?key=wrong").status_code == 401


def test_dataset_video_accepts_header_key(tmp_path, monkeypatch):
    from fortevision import config

    (tmp_path / "a.mov").write_bytes(b"abc")
    monkeypatch.setattr(config, "DATASET_DIR", tmp_path)
    assert client.get("/dataset/videos/a.mov", headers=HEADERS).content == b"abc"
    assert client.get("/dataset/videos/a.mov", headers={"X-API-Key": "nope"}).status_code == 401


def test_dataset_video_rejects_bad_names():
    assert client.get(f"/dataset/videos/..%2Fsecret.mov?key={API_KEY}").status_code in (400, 404)
    assert client.get(f"/dataset/videos/passwd?key={API_KEY}").status_code == 400
    assert client.get(f"/dataset/videos/missing.mov?key={API_KEY}").status_code == 404


def test_dataset_video_served_with_range(tmp_path, monkeypatch):
    from fortevision import config

    (tmp_path / "a.mov").write_bytes(b"0123456789")
    monkeypatch.setattr(config, "DATASET_DIR", tmp_path)
    full = client.get(f"/dataset/videos/a.mov?key={API_KEY}")
    assert full.status_code == 200 and full.content == b"0123456789"
    part = client.get(f"/dataset/videos/a.mov?key={API_KEY}", headers={"Range": "bytes=2-4"})
    assert part.status_code == 206 and part.content == b"234"


def test_static_assets_served():
    assert client.get("/static/journal.json").status_code == 200


def test_ui_assets_revalidate():
    assert client.get("/static/js/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_index_assets_are_versioned():
    html = client.get("/").text
    assert "__V__" not in html and "/static/css/app.css?v=" in html and "/static/js/app.js?v=" in html
