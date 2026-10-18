"""Build static/journal.json from the open labels and the evaluation results.

Usage: python scripts/build_journal.py
"""

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "open" / "open_labels.xlsx"
VIDEOS = ROOT / "open" / "videos"
EVAL = ROOT / "evaluation_results.json"
OUT = ROOT / "src" / "fortevision" / "static" / "journal.json"

NOTE = "Замер на обучающих видео: классификатор подобран на них же, поэтому оценка завышена. На новых видео ожидается ниже."


def norm(name: str) -> str:
    """Latin X and Cyrillic Х are mixed in the dataset; compare on the Latin form."""
    return unicodedata.normalize("NFC", str(name)).strip().replace("Х", "X").replace("х", "x")


def parse_time(text) -> float | None:
    if not text:
        return None
    t = str(text)
    minutes = re.search(r"(\d+)\s*мин", t)
    seconds = re.search(r"(\d+)\s*сек", t)
    if not (minutes or seconds):
        return None
    return float((int(minutes.group(1)) if minutes else 0) * 60 + (int(seconds.group(1)) if seconds else 0))


def main():
    files = {norm(p.stem): p.name for p in VIDEOS.glob("*.mov")}
    evaluation = json.loads(EVAL.read_text(encoding="utf-8"))
    predicted = {norm(r["video"]): r for r in evaluation["results"]}

    rows = []
    for name, time_text, label in list(openpyxl.load_workbook(LABELS).active.iter_rows(min_row=2, values_only=True)):
        if not name:
            continue
        key = norm(name)
        if key not in files:
            print(f"skip {name}: no video file")
            continue
        truth = "violation" if "без" not in str(label) else "no_violation"
        pred = predicted.get(key, {})
        pred_verdict = pred.get("pred_verdict")
        true_time, pred_time = parse_time(time_text), pred.get("pred_time")
        outcome = None
        if pred_verdict:
            outcome = {
                ("violation", "violation"): "TP",
                ("no_violation", "no_violation"): "TN",
                ("no_violation", "violation"): "FP",
                ("violation", "no_violation"): "FN",
            }[(truth, pred_verdict)]
        rows.append({
            "video": key,
            "file": files[key],
            "label": truth,
            "true_time": true_time,
            "pred_verdict": pred_verdict,
            "pred_time": pred_time,
            "delta": round(abs(pred_time - true_time), 1) if None not in (true_time, pred_time) else None,
            "outcome": outcome,
        })

    journal = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": NOTE,
        "summary": evaluation["summary"],
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
