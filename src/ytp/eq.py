"""3-knob graphic EQ: persistence, interpolation for the preview chart,
and the actual mpv audio-filter chain."""

import json
import math
import os

from .config import CONFIG_DIR

# A classic 3-knob graphic EQ: the user only tunes bass/mid/treble. The
# 6-band interpolated curve (interpolate_eq) is used purely to draw the
# preview bar chart; the filter chain actually applied (build_af) uses
# shelving filters and a protective high-pass instead -- see build_af for
# why that matters more than it looks on small speakers.
EQ_PATH = os.path.join(CONFIG_DIR, "eq.json")
EQ_ORDER = ["bass", "mid", "treble"]
EQ_KEYPOINT_HZ = {"bass": 150, "mid": 1000, "treble": 8000}
EQ_BANDS_HZ = [32, 100, 315, 1000, 3150, 8000]
EQ_RANGE_DB = 12


def load_eq():
    gains = {name: 0.0 for name in EQ_ORDER}
    try:
        with open(EQ_PATH) as f:
            data = json.load(f)
        for name in EQ_ORDER:
            if name in data:
                gains[name] = float(data[name])
    except (OSError, ValueError, KeyError):
        pass
    return gains


def save_eq(gains):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(EQ_PATH, "w") as f:
        json.dump(gains, f)


def interpolate_eq(gains):
    """Gain at each of EQ_BANDS_HZ, linearly interpolated in log2(freq)
    between the bass/mid/treble keypoints (flat beyond the outer ones)."""
    points = sorted(
        ((math.log2(hz), gains[name]) for name, hz in EQ_KEYPOINT_HZ.items()),
        key=lambda p: p[0],
    )
    result = {}
    for hz in EQ_BANDS_HZ:
        lf = math.log2(hz)
        if lf <= points[0][0]:
            result[hz] = points[0][1]
        elif lf >= points[-1][0]:
            result[hz] = points[-1][1]
        else:
            for (lf0, g0), (lf1, g1) in zip(points, points[1:]):
                if lf0 <= lf <= lf1:
                    t = 0 if lf1 == lf0 else (lf - lf0) / (lf1 - lf0)
                    result[hz] = g0 + t * (g1 - g0)
                    break
    return result


def build_af(gains):
    """Small speakers physically can't move enough air to reproduce deep
    bass, so a naive bass-band boost just drives the cone harder at
    frequencies it can't radiate -- the classic result is buzzing/rattling
    distortion, not more bass. Two standard fixes, applied unconditionally:

    - A high-pass rolls off content the driver can't reproduce anyway
      (below ~70 Hz). That content was pure wasted excursion and a source
      of intermodulation distortion in the mids; removing it also frees up
      headroom for the boost below.
    - The bass and treble knobs use shelving filters (ffmpeg's `bass` /
      `treble`, i.e. low/high shelf) centered in the 100-200 Hz "warmth"
      range rather than a narrow high-Q peak or true sub-bass. A shelf
      raises a broad, gentle slope instead of ringing at one frequency,
      which is both what actually reads as "more bass" on a small driver
      and far less likely to distort or resonate.
    """
    bass, mid, treble = gains["bass"], gains["mid"], gains["treble"]
    parts = [
        "highpass=f=70",
        f"bass=g={bass:.2f}:f=150:w=0.7",
        f"equalizer=f=1000:width_type=o:width=1:g={mid:.2f}",
        f"treble=g={treble:.2f}:f=8000:w=0.7",
    ]
    return "lavfi=[" + ",".join(parts) + "]"
