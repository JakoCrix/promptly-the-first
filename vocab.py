import json
import os
import random
from datetime import datetime

from config import DATA_DIR, NEVER_SEEN_SECONDS, RETIREMENT_THRESHOLD, VOCAB_TOPIC


def vocab_path(chat_id: int) -> str:
    return os.path.join(DATA_DIR, f"{VOCAB_TOPIC}_{chat_id}.json")


def load_vocab(chat_id: int) -> dict | None:
    try:
        with open(vocab_path(chat_id), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_vocab(chat_id: int, data: dict) -> None:
    path = vocab_path(chat_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _elapsed_seconds(word: dict, now: datetime) -> float:
    if word["last_seen"] is None:
        return float(NEVER_SEEN_SECONDS)
    delta = now - datetime.fromisoformat(word["last_seen"])
    return max(0.0, delta.total_seconds())


def _weight(word: dict, now: datetime) -> float:
    return (1.0 / (word["mastery_score"] + 1)) * _elapsed_seconds(word, now)


def pick_word(chat_id: int) -> dict | None:
    data = load_vocab(chat_id)
    if data is None:
        return None
    eligible = [w for w in data["words"] if w["mastery_score"] < RETIREMENT_THRESHOLD]
    if not eligible:
        return None
    now = datetime.now()
    weights = [_weight(w, now) for w in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]


def record_feedback(chat_id: int, word_id: str, result: str) -> bool:
    data = load_vocab(chat_id)
    if data is None:
        return False
    word = next((w for w in data["words"] if w["id"] == word_id), None)
    if word is None:
        return False
    now = datetime.now()
    word["last_seen"] = now.isoformat()
    if result == "known":
        word["mastery_score"] += 1
    elif result == "forgot":
        word["mastery_score"] = max(0, word["mastery_score"] - 1)
    word["history"].append({"timestamp": now.isoformat(), "result": result})
    save_vocab(chat_id, data)
    return True
