"""Paths and environment-derived settings shared across modules."""

import os
from pathlib import Path

# The repo's own config/ directory (speaker_*.txt art, eq.json) is the
# default, so cloning the repo and editing those files just works -- no
# hidden ~/.config directory to go hunting for. Set YTP_CONFIG_DIR to
# point somewhere else instead (e.g. if ytp is installed standalone,
# away from a checkout of the repo).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = Path(os.environ.get("YTP_CONFIG_DIR", _REPO_ROOT / "config"))
ART_PATH = CONFIG_DIR / "speaker_tall.txt"
MED_ART_PATH = CONFIG_DIR / "speaker_med.txt"
SMALL_ART_PATH = CONFIG_DIR / "speaker_small.txt"

# Runtime state (favorites, play history) isn't an editable default like
# the art/eq files above, so it doesn't belong in CONFIG_DIR -- writing it
# there would drop an untracked file into a checkout of the repo. It goes
# in the standard XDG data location instead. Set YTP_DATA_DIR to point
# somewhere else.
_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = Path(os.environ.get("YTP_DATA_DIR", _DATA_HOME / "ytp"))

# Optional path to mpv-mpris's mpris.so, so hardware/OS media keys
# (play/pause/next/previous) reach mpv directly -- our progress bar just
# polls mpv's own state, so it stays in sync regardless of whether space
# or a media key changed it. Unset (or a nonexistent path) just means no
# MPRIS integration; everything else still works. On NixOS this is
# typically `${pkgs.mpvScripts.mpris}/share/mpv/scripts/mpris.so`.
MPRIS_SCRIPT = os.environ.get("YTP_MPRIS_SCRIPT", "")
