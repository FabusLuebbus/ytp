"""Persistent favorite-track storage."""

import json
import os

from .config import DATA_DIR

FAVORITES_PATH = os.path.join(DATA_DIR, "favorites.json")


def load_favorites():
    """Return favorite tracks, keyed by YouTube id."""
    try:
        with open(FAVORITES_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_favorites(favorites):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FAVORITES_PATH, "w") as f:
        json.dump(favorites, f, indent=2)


def toggle_favorite(favorites, track):
    """Toggle *track* and return whether it is now a favorite."""
    track_id = track.get("id")
    if not track_id:
        return False
    if track_id in favorites:
        del favorites[track_id]
        favorite = False
    else:
        favorites[track_id] = track
        favorite = True
    save_favorites(favorites)
    return favorite
