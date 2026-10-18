"""Per-frame visual features from a VLM, and temporal pooling into per-video features.

A single frame plus one short yes/no question is something a 7-8B VLM answers reliably; judging a
whole sequence at once is not. Each frame is asked a fixed list of questions, the probability of
"Yes" is read from the logits (no free-text generation), and the series are pooled over time.
"""

from typing import Dict, List, Sequence

import numpy as np
from PIL import Image

SCENE = (
    "This is a top-down CCTV frame of a small bank cash room. There is a desk with a cash-counting "
    "machine and a laptop, a grey safe and a glass door. The cashier works at the desk; a client or "
    "collector may sit on the other side of the desk. "
)

QUESTIONS: Dict[str, str] = {
    "cashier_seated": "Is someone sitting at the desk working (the cashier)?",
    "desk_unattended": "Is the desk completely without a person sitting at it?",
    "standing_person": "Is any person standing up or walking inside the room?",
    "person_beside_cashier": "Is a person standing right next to or behind the seated cashier?",
    "cash_on_desk": "Are there banknotes, money or a money bag lying on the desk?",
    "cash_in_hands": "Is someone holding banknotes in their hands above the desk?",
    "personal_items": "Is there a jacket, a personal bag, a cup, a bottle or food on the desk?",
    "safe_open": "Is the door of the grey safe open?",
    "cashier_standing": "Is the cashier standing up or walking away from the desk?",
    "two_plus_people": "Are at least two people visible in the room?",
    "client_seated": "Is a person sitting on the opposite side of the desk from the cashier?",
    "cash_in_machine": "Is someone putting banknotes into the counting machine?",
    "bag_on_desk": "Is there a bag lying on the desk?",
    "hands_on_money": "Are someone's hands touching banknotes?",
}
QUESTION_KEYS: List[str] = list(QUESTIONS)


def _prompt(question: str) -> str:
    return f"{SCENE}{question} Answer with one word: Yes or No."


def frame_probabilities(model, processor, device: str, image: Image.Image) -> np.ndarray:
    """P(Yes) for every question on one frame (one batched forward pass, no generation)."""
    import torch

    tok = processor.tokenizer
    tok.padding_side = "left"
    yes_id = tok.encode("Yes", add_special_tokens=False)[0]
    no_id = tok.encode("No", add_special_tokens=False)[0]

    texts = []
    for q in QUESTIONS.values():
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": _prompt(q)}]}]
        texts.append(processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True))
    probs = []
    for start in range(0, len(texts), 16):  # one batch per frame; the cap bounds VRAM
        chunk = texts[start : start + 16]
        inputs = processor(text=chunk, images=[image] * len(chunk), padding=True, return_tensors="pt").to(device)
        with torch.no_grad():
            logits = model(**inputs, logits_to_keep=1).logits[:, -1, :].float()  # last token only
        pair = torch.stack([logits[:, yes_id], logits[:, no_id]], dim=1)
        probs.append(torch.softmax(pair, dim=1)[:, 0].cpu().numpy())
    return np.concatenate(probs)


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def smooth(x: np.ndarray) -> np.ndarray:
    """3-second moving average suppresses single-frame noise."""
    return np.convolve(x, np.ones(3) / 3, mode="same") if len(x) >= 3 else x


def _transitions(mask: np.ndarray) -> int:
    return int(np.abs(np.diff(mask.astype(int))).sum()) if len(mask) > 1 else 0


def pool_features(series: np.ndarray) -> Dict[str, float]:
    """series: (T, Q) matrix of P(Yes) at ~1 fps -> flat dict of video-level features."""
    sm = {name: smooth(series[:, i]) for i, name in enumerate(QUESTION_KEYS)}
    out: Dict[str, float] = {}
    for n, v in sm.items():
        out[f"{n}_max"] = float(v.max())
        out[f"{n}_mean"] = float(v.mean())
        out[f"{n}_run"] = float(_longest_run(v > 0.5)) / max(len(v), 1)

    def g(key):
        return sm.get(key, np.zeros_like(next(iter(sm.values()))))

    # Compound events
    out["left_cash_max"] = float(np.minimum(g("desk_unattended"), g("cash_on_desk")).max())
    out["left_items_max"] = float(np.minimum(g("desk_unattended"), g("personal_items")).max())
    out["left_safe_max"] = float(np.minimum(g("desk_unattended"), g("safe_open")).max())
    out["beside_standing_max"] = float(np.minimum(g("person_beside_cashier"), g("standing_person")).max())
    out["hand_count_max"] = float((g("cash_in_hands") * (1.0 - g("cash_in_machine"))).max())
    out["unattended_frac"] = float((g("desk_unattended") > 0.5).mean())
    out["seat_transitions"] = float(_transitions(g("cashier_seated") > 0.5)) / max(len(g("cashier_seated")), 1)
    out["people_transitions"] = float(_transitions(g("two_plus_people") > 0.5)) / max(len(g("cashier_seated")), 1)
    return out
