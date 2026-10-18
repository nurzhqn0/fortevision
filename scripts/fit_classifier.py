"""Fit the video-level violation classifier on extracted per-frame features.

    python scripts/fit_classifier.py features_q3.json [--out src/fortevision/classifier.json]

Pure numpy (L2 logistic regression). Reports leave-one-out CV so the number is an honest
estimate of unseen-video performance, then fits on all clips and writes the weights.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fortevision.features import QUESTION_KEYS, pool_features  # noqa: E402


def load_truth(path="evaluation_results.json"):
    norm = lambda n: n.replace("X", "Х").replace("x", "х")  # labels mix Latin and Cyrillic "X"
    return {norm(r["video"]): (r["true_verdict"] == "violation", r["true_time"]) for r in json.load(open(path))["results"]}


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logreg(X, y, l2=1.0, iters=2000, lr=0.1):
    w = np.zeros(X.shape[1])
    b = 0.0
    for _ in range(iters):
        p = sigmoid(X @ w + b)
        gw = X.T @ (p - y) / len(y) + l2 * w / len(y)
        gb = float(np.mean(p - y))
        w -= lr * gw
        b -= lr * gb
    return w, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("features")
    ap.add_argument("--out", default=None)
    ap.add_argument("--l2", type=float, default=30.0)
    ap.add_argument("--th", type=float, default=0.45)
    ap.add_argument("--select", default="", help="comma list of feature name substrings to keep")
    args = ap.parse_args()

    data = json.load(open(args.features))
    assert data["questions"] == QUESTION_KEYS, "feature file was made with a different question list"
    truth = load_truth()
    names = [n for n in data["series"] if n in truth]

    rows, ys = [], []
    for n in names:
        arr = np.array(data["series"][n])
        rows.append(pool_features(arr[:, 1:]))
        ys.append(float(truth[n][0]))
    feat_names = list(rows[0])
    if args.select:
        keys = args.select.split(",")
        feat_names = [f for f in feat_names if any(k in f for k in keys)]
    X = np.array([[r[f] for f in feat_names] for r in rows])
    y = np.array(ys)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xs = (X - mu) / sd

    # Leave-one-out CV
    probs = np.zeros(len(y))
    for i in range(len(y)):
        m = np.arange(len(y)) != i
        mu_i, sd_i = X[m].mean(0), X[m].std(0) + 1e-6
        w, b = fit_logreg((X[m] - mu_i) / sd_i, y[m], l2=args.l2)
        probs[i] = sigmoid(((X[i] - mu_i) / sd_i) @ w + b)
    best = None
    print("threshold sweep (leave-one-out):")
    for th in (0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6):
        pred = probs >= th
        tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
        fn = int((~pred & (y == 1)).sum()); tn = int((~pred & (y == 0)).sum())
        acc = (tp + tn) / len(y)
        print(f"  th={th:.2f} acc={acc:.3f} TP={tp} FP={fp} FN={fn} TN={tn}")
    from math import isnan
    th = args.th
    pred = probs >= th
    print(f"\nLOO accuracy @ {th}: {((pred == (y == 1)).mean()):.3f} over {len(y)} clips")
    for n, p, t in zip(names, probs, y):
        if (p >= th) != (t == 1):
            print(f"  miss {n}: p={p:.2f} truth={'violation' if t else 'no_violation'}")

    w, b = fit_logreg(Xs, y, l2=args.l2)
    top = sorted(zip(feat_names, w), key=lambda x: -abs(x[1]))[:10]
    print("\ntop weights:", [(n, round(float(v), 2)) for n, v in top])
    if args.out:
        json.dump({
            "features": feat_names, "mean": mu.tolist(), "std": sd.tolist(),
            "weights": w.tolist(), "bias": b, "threshold": th, "model": data.get("model"),
        }, open(args.out, "w"), indent=1)
        print("wrote", args.out)


if __name__ == "__main__":
    main()
