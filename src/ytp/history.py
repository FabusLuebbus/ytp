"""Persistent playback-history storage."""

import json
import os

from .config import DATA_DIR

HISTORY_PATH = os.path.join(DATA_DIR, "history.json")


def load_history():
    """Return previously played tracks, oldest first (most recent last)."""
    try:
        with open(HISTORY_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_history(history):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
