"""Beat detection for the "beat" visual mode."""

import os
import subprocess
import tempfile


def analyze_beat(url):
    """Download a short clip and run aubio's beat tracker (a standard
    spectral-flux onset/tempo algorithm -- see aubiotrack(1)) to get an
    inter-beat interval and a phase anchor. The render loop extrapolates a
    steady beat grid from that rather than tracking beats for the whole
    track, which would mean downloading and analyzing the entire thing."""
    with tempfile.TemporaryDirectory() as tmp:
        clip_tpl = os.path.join(tmp, "clip.%(ext)s")
        wav = os.path.join(tmp, "clip.wav")
        try:
            subprocess.run(
                ["yt-dlp", "--no-warnings", "-f", "bestaudio",
                 "--download-sections", "*0-45", "-x", "--audio-format", "wav",
                 "-o", clip_tpl, url],
                capture_output=True, check=True, timeout=60,
            )
            out = subprocess.run(
                ["aubiotrack", "-i", wav],
                capture_output=True, text=True, check=True, timeout=30,
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
            return None
    times = sorted(float(x) for x in out.split() if x.strip())
    if len(times) < 4:
        return None
    intervals = sorted(b - a for a, b in zip(times, times[1:]) if 0.25 < b - a < 1.5)
    if not intervals:
        return None
    interval = intervals[len(intervals) // 2]  # median: robust to missed/doubled detections
    return {"interval": interval, "phase": times[-1]}
