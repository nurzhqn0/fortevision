"""Evaluation script for ForteVision against labeled training dataset.

Computes:
- Verdict Accuracy (violation vs no_violation)
- Precision, Recall, F1
- Timecode Localization Error (|pred_time - true_time| <= tolerance_sec)
- Detailed per-video breakdown table
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
import requests
from tqdm import tqdm


def parse_label_timecode(time_str: Optional[str]) -> Optional[float]:
    """Parse time string from open_labels.xlsx (e.g. '22 секунда', '1минута 8 секунда')."""
    if not time_str:
        return None

    cleaned = str(time_str).lower().strip()
    minutes = 0.0
    seconds = 0.0

    min_match = re.search(r"(\d+)\s*(?:мин|минута|минуты|минут)", cleaned)
    if min_match:
        minutes = float(min_match.group(1))

    sec_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:сек|секунда|секунды|секунд)", cleaned)
    if sec_match:
        seconds = float(sec_match.group(1))

    if not min_match and not sec_match:
        digits_match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
        if digits_match:
            seconds = float(digits_match.group(1))

    total = minutes * 60.0 + seconds
    return total if total > 0 else None


def load_ground_truth(labels_path: str) -> Dict[str, Dict]:
    """Load labels spreadsheet into structured dictionary."""
    wb = openpyxl.load_workbook(labels_path)
    ws = wb.active

    ground_truth = {}
    for row in ws.iter_rows(values_only=True):
        if not row or not row[0] or "наименование" in str(row[0]).lower():
            continue

        raw_name = str(row[0]).strip()
        time_str = row[1]
        verdict_str = str(row[2]).strip().lower() if row[2] else ""

        is_violation = "нарушение" in verdict_str and "без" not in verdict_str
        expected_time = parse_label_timecode(time_str) if is_violation else None

        # Normalize video filename (handle cyrillic X vs latin X)
        ground_truth[raw_name] = {
            "name": raw_name,
            "has_violation": is_violation,
            "verdict": "violation" if is_violation else "no_violation",
            "timecode_sec": expected_time,
            "raw_time_str": str(time_str),
        }

    return ground_truth


def find_video_file(videos_dir: Path, video_name: str) -> Optional[Path]:
    """Find video file matching name regardless of Latin/Cyrillic 'X' character."""
    # Direct check
    candidate = videos_dir / f"{video_name}.mov"
    if candidate.exists():
        return candidate

    # Search directory for match
    for f in videos_dir.glob("*.mov"):
        # Normalize latin X vs cyrillic Х
        stem_norm = f.stem.replace("Х", "X").replace("х", "x").strip()
        name_norm = video_name.replace("Х", "X").replace("х", "x").strip()
        if stem_norm == name_norm:
            return f

    return None


def evaluate_via_api(
    api_url: str,
    api_key: str,
    videos_dir: str,
    labels_file: str,
    limit: Optional[int] = None,
    time_tolerance_sec: float = 6.0,
    concurrency: int = 4,
):
    """Run full evaluation suite through HTTP API."""
    videos_path = Path(videos_dir)
    gt = load_ground_truth(labels_file)

    items = list(gt.items())
    if limit:
        items = items[:limit]

    print(f"\nEvaluating ForteVision against {len(items)} videos via API: {api_url}")
    print("=" * 80)

    tp, fp, tn, fn = 0, 0, 0, 0
    time_correct = 0
    results_table = []

    # Warmup ping to ensure container is initialized
    print(f"Pinging endpoint {api_url} to ensure warm container...")
    try:
        requests.get(f"{api_url}/docs", timeout=60)
    except Exception as e:
        print(f"Warmup ping warning: {e}")

    def process_item(item_data):
        name, truth = item_data
        video_file = find_video_file(videos_path, name)
        if not video_file or not video_file.exists():
            print(f"Warning: File for {name} not found, skipping.")
            return None

        try:
            # 1. POST /api/v1/analyze
            post_resp = None
            for attempt in range(3):
                try:
                    with open(video_file, "rb") as vf:
                        post_resp = requests.post(
                            f"{api_url}/api/v1/analyze",
                            headers={"X-API-Key": api_key},
                            files={"file": (video_file.name, vf, "video/quicktime")},
                            timeout=180,
                        )
                    if post_resp.status_code == 200:
                        break
                except requests.exceptions.RequestException as e:
                    if attempt < 2:
                        time.sleep(5)
                        continue
                    raise

            if not post_resp or post_resp.status_code != 200:
                print(f"API Error ({getattr(post_resp, 'status_code', 'None')}) on {name}")
                return None

            job_id = post_resp.json()["job_id"]

            # 2. Poll GET /api/v1/jobs/{job_id}
            job_result = None
            for _ in range(60):
                time.sleep(2)
                get_resp = requests.get(
                    f"{api_url}/api/v1/jobs/{job_id}",
                    headers={"X-API-Key": api_key},
                    timeout=15,
                )
                if get_resp.status_code == 200:
                    status_data = get_resp.json()
                    if status_data.get("status") in ["done", "failed"]:
                        job_result = status_data
                        break

            if not job_result:
                print(f"Timeout waiting for job {job_id} on {name}")
                return None

            pred_verdict = job_result.get("verdict", "no_violation")
            pred_violations = job_result.get("violations", [])
            pred_time = pred_violations[0]["timecode_sec"] if pred_violations else None

            true_verdict = truth["verdict"]
            is_correct_verdict = pred_verdict == true_verdict

            return {
                "video": name,
                "true_verdict": true_verdict,
                "pred_verdict": pred_verdict,
                "true_time": truth["timecode_sec"],
                "pred_time": pred_time,
                "verdict_match": is_correct_verdict,
            }
        except Exception as e:
            print(f"Exception evaluating {name}: {e}")
            return None

    import concurrent.futures
    raw_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {executor.submit(process_item, item): item for item in items}
        for future in tqdm(concurrent.futures.as_completed(future_map), total=len(items), desc="Evaluating videos"):
            res = future.result()
            if res:
                raw_results.append(res)

    # Sort results by original item order
    item_order = {item[0]: idx for idx, item in enumerate(items)}
    results_table = sorted(raw_results, key=lambda r: item_order.get(r["video"], 999))

    for r in results_table:
        true_verdict = r["true_verdict"]
        pred_verdict = r["pred_verdict"]
        if true_verdict == "violation" and pred_verdict == "violation":
            tp += 1
            if r["true_time"] is not None and r["pred_time"] is not None:
                delta = abs(r["true_time"] - r["pred_time"])
                if delta <= time_tolerance_sec:
                    time_correct += 1
        elif true_verdict == "no_violation" and pred_verdict == "violation":
            fp += 1
        elif true_verdict == "no_violation" and pred_verdict == "no_violation":
            tn += 1
        elif true_verdict == "violation" and pred_verdict == "no_violation":
            fn += 1

    # Metrics
    total = tp + fp + tn + fn
    accuracy = (tp + tn) / total if total > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    time_acc = (time_correct / tp) if tp > 0 else 0.0

    print("\nDetailed Per-Video Results:")
    print(f"{'Video':<10} | {'True Verdict':<14} | {'Pred Verdict':<14} | {'True Time':<10} | {'Pred Time':<10} | {'Match'}")
    print("-" * 75)
    for r in results_table:
        m = "✅" if r["verdict_match"] else "❌"
        tt = f"{r['true_time']}s" if r['true_time'] is not None else "-"
        pt = f"{r['pred_time']}s" if r['pred_time'] is not None else "-"
        print(f"{r['video']:<10} | {r['true_verdict']:<14} | {r['pred_verdict']:<14} | {tt:<10} | {pt:<10} | {m}")

    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY REPORT:")
    print("=" * 80)
    print(f"Total Videos Tested:       {total}")
    print(f"True Positives (TP):       {tp}")
    print(f"True Negatives (TN):       {tn}")
    print(f"False Positives (FP):      {fp}")
    print(f"False Negatives (FN):      {fn}")
    print("-" * 40)
    print(f"Verdict Accuracy:          {accuracy * 100:.1f}%")
    print(f"Precision:                 {precision * 100:.1f}%")
    print(f"Recall:                    {recall * 100:.1f}%")
    print(f"F1 Score:                  {f1 * 100:.1f}%")
    print(f"Timecode Accuracy (±{time_tolerance_sec:.0f}s): {time_acc * 100:.1f}%")
    print("=" * 80)

    # Save to evaluation_results.json
    output_data = {
        "summary": {
            "total": total,
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "time_accuracy": round(time_acc, 4),
        },
        "results": results_table,
    }
    try:
        import json
        with open("evaluation_results.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print("Saved full evaluation data to evaluation_results.json")
    except Exception as e:
        print(f"Warning: Could not save evaluation_results.json: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate ForteVision API against dataset")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="Base API URL")
    parser.add_argument("--api-key", default="fortevision-secret-key-2026", help="X-API-Key header")
    parser.add_argument("--videos", default="open/videos", help="Path to videos directory")
    parser.add_argument("--labels", default="open/open_labels.xlsx", help="Path to labels xlsx")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of videos tested")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of concurrent worker threads")
    args = parser.parse_args()

    evaluate_via_api(
        api_url=args.api_url,
        api_key=args.api_key,
        videos_dir=args.videos,
        labels_file=args.labels,
        limit=args.limit,
        concurrency=args.concurrency,
    )
