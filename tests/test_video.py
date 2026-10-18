"""Unit tests for fortevision.video module."""

from pathlib import Path
from fortevision.video import (
    get_video_metadata,
    extract_frames,
    extract_dense_window,
    pick_evidence_frames,
)

def test_video_processing():
    test_video = Path("open/videos/X-1.mov")
    assert test_video.exists(), f"Sample video {test_video} not found"

    # Test metadata
    meta = get_video_metadata(test_video)
    print(f"Metadata for {test_video}: {meta}")
    assert meta.duration_sec > 0
    assert meta.fps > 0
    assert meta.width > 0
    assert meta.height > 0

    # Test adaptive frame extraction
    frames = extract_frames(test_video, target_fps=1.0)
    print(f"Extracted {len(frames)} frames at ~1.0 fps")
    assert len(frames) > 0
    first_frame = frames[0]
    print(f"First frame: timestamp={first_frame.timestamp_sec}s, shape={first_frame.shape}")
    assert first_frame.timestamp_sec == 0.0

    # Test dense window extraction around 10.0s
    dense = extract_dense_window(test_video, center_time_sec=10.0, window_sec=4.0, dense_fps=2.0)
    print(f"Extracted {len(dense)} dense frames around 10.0s")
    assert len(dense) > 0
    for d in dense:
        print(f"  dense frame ts: {d.timestamp_sec}s")

    # Test evidence frame formatting
    evidence = pick_evidence_frames(frames, violation_time_sec=10.0, count=2)
    assert len(evidence) == 2
    for ev in evidence:
        assert "timestamp_sec" in ev
        assert "image_base64" in ev
        assert len(ev["image_base64"]) > 100
        print(f"Evidence frame at {ev['timestamp_sec']}s, base64 length: {len(ev['image_base64'])} chars")

    print("\nAll video processing tests PASSED successfully!")

if __name__ == "__main__":
    test_video_processing()
