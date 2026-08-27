"""yt-dlp-backed lookups: a single video, YouTube's own "mix", a channel's
videos, and search results."""

import json
import subprocess


def run_json(*args):
    out = subprocess.run(
        ["yt-dlp", "--no-warnings", *args],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def parse_entries(entries, exclude_id=None, fallback_channel=None, fallback_channel_url=None):
    out = []
    for e in entries:
        if exclude_id and e.get("id") == exclude_id:
            continue
        out.append({
            "id": e.get("id"),
            "title": e.get("title", "?"),
            "channel": e.get("channel") or e.get("uploader") or fallback_channel or "?",
            "channel_url": e.get("channel_url") or e.get("uploader_url") or fallback_channel_url,
            "duration": e.get("duration"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
        })
    return out


def fetch_video(url):
    info = run_json("-j", "--flat-playlist", url)
    return {
        "id": info["id"],
        "title": info.get("title", "?"),
        "channel": info.get("channel") or info.get("uploader") or "?",
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "duration": info.get("duration"),
        "url": info.get("webpage_url") or url,
    }


def fetch_mix(video_id):
    """YouTube's own "up next" mix for a video (the RD<id> radio playlist)."""
    data = run_json(
        "--flat-playlist", "--playlist-items", "1-20", "-J",
        f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}",
    )
    return parse_entries(data.get("entries", []), exclude_id=video_id)


def fetch_channel_videos(channel_url, exclude_id):
    if not channel_url:
        return []
    data = run_json(
        "--flat-playlist", "--playlist-items", "1-20", "-J",
        channel_url.rstrip("/") + "/videos",
    )
    # A channel's /videos listing carries the channel name/url once at the
    # top level, not per-entry -- unlike a mix or search result.
    return parse_entries(
        data.get("entries", []), exclude_id=exclude_id,
        fallback_channel=data.get("channel") or data.get("uploader"),
        fallback_channel_url=data.get("channel_url") or data.get("uploader_url"),
    )


def fetch_search(query):
    data = run_json("--flat-playlist", "-J", f"ytsearch15:{query}")
    return parse_entries(data.get("entries", []))
