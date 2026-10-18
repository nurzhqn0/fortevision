"""Extract per-frame VLM yes/no probabilities for every clip and save them to a JSON file.

    modal run scripts/extract_features.py --model Qwen/Qwen3-VL-8B-Instruct --out features_q3.json

The raw series (clip -> list of [t, p_q1..p_qN]) are what `scripts/fit_classifier.py` trains on.
"""

import glob
import json
import os

import modal

from fortevision.app import app_image, model_volume

app = modal.App("fortevision-features")


def _clips():
    return sorted(glob.glob("open/videos/*.mov"))


def _norm(name: str) -> str:
    return name.replace("X", "Х").replace("x", "х")


@app.function(image=app_image, gpu=os.environ.get("FV_GPU", "A10G"), volumes={"/models": model_volume}, timeout=3000)
def extract(names: list, model_id: str, out_name: str, fps: float = 1.0, max_frames: int = 150):
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor

    from fortevision.analyzer import _to_vlm_image
    from fortevision.features import QUESTION_KEYS, frame_probabilities
    from fortevision.video import extract_frames

    cache = "/models/huggingface"
    model = AutoModelForImageTextToText.from_pretrained(
        model_id, torch_dtype=torch.bfloat16, device_map="auto", cache_dir=cache
    )
    model_volume.commit()
    processor = AutoProcessor.from_pretrained(model_id, cache_dir=cache)

    out = {"questions": QUESTION_KEYS, "series": {}}
    model_volume.reload()
    for name in names:
        data = open(f"/models/data/{name}.mov", "rb").read()
        frames = extract_frames(data, target_fps=fps, max_frames=max_frames, adaptive=False)
        rows = []
        for f in frames:
            p = frame_probabilities(model, processor, "cuda", _to_vlm_image(f))
            rows.append([f.timestamp_sec] + [round(float(x), 4) for x in p])
        out["series"][name] = rows
        print(f"{name}: {len(rows)} frames", flush=True)
    out["model"] = model_id
    # Saved on the volume so a detached run survives the local client disconnecting
    os.makedirs("/models/features", exist_ok=True)
    json.dump(out, open(f"/models/features/{out_name}", "w"))
    model_volume.commit()
    print(f"SAVED /models/features/{out_name}", flush=True)
    return out_name


@app.local_entrypoint()
def main(model: str = "Qwen/Qwen3-VL-8B-Instruct", out: str = "features.json", limit: int = 0):
    clips = _clips()[: limit or None]
    names = []
    # Clips go to the private volume once; shipping them as call arguments exceeds payload limits
    with model_volume.batch_upload(force=True) as batch:
        for p in clips:
            name = _norm(os.path.basename(p)[:-4])
            batch.put_file(p, f"/data/{name}.mov")
            names.append(name)
    extract.remote(names, model, out)
    print(f"done; download with: modal volume get fortevision-models /features/{out} .")
