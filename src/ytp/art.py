"""ASCII-art speaker loading and left/right mirroring."""

import os

from .config import CONFIG_DIR

# Each file draws exactly one speaker; the program mirrors and pairs it
# left/right (see pair_speaker), so editing one half is enough.
DEFAULT_ART = r'''
  ╭───────────────────────────╮
 ╱▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓╱│
╔═══════════════════════════╗▒│
║▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓║▒│
║▓┌───────────────────────┐▓║▒│
║▓│                       │▓║▒│
║▓│          .-.          │▓║▒│
║▓│         ( • )         │▓║▒│
║▓│          '-'          │▓║▒│
║▓│                       │▓║▒│
║▓│        .-"""-.        │▓║▒│
║▓│       /  .-.  \       │▓║▒│
║▓│      |  ( • )  |      │▓║▒│
║▓│       \  '-'  /       │▓║▒│
║▓│        '-...-'        │▓║▒│
║▓│                       │▓║▒│
║▓│      .-"""""""-.      │▓║▒│
║▓│    .'  .-"""-.  '.    │▓║▒│
║▓│   /  .'  .-.  '.  \   │▓║▒│
║▓│  |  |   ( • )   |  |  │▓║▒│
║▓│   \  '.  '-'  .'  /   │▓║▒│
║▓│    '.  '-...-'  .'    │▓║▒│
║▓│      '-.......-'      │▓║▒│
║▓│                       │▓║▒│
║▓│      ╭─────────╮      │▓║▒│
║▓│      │▒▒▒▒▒▒▒▒▒│      │▓║▒│
║▓│      ╰─────────╯      │▓║▒│
║▓│                       │▓║▒│
║▓│  •                    │▓║▒│
║▓└───────────────────────┘▓║▒╯
║▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓║╱
╚═══════════════════════════╝
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  ▼                       ▼
'''.strip("\n")

DEFAULT_MED_ART = r'''
  ╭───────────────────────────╮
 ╱▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓╱│
╔═══════════════════════════╗▒│
║▓┌───────────────────────┐▓║▒│
║▓│        (( • ))        │▓║▒│
║▓│        .-"""-.        │▓║▒│
║▓│      |  ( • )  |      │▓║▒│
║▓│        '-...-'        │▓║▒│
║▓│     .-"""""""""-.     │▓║▒│
║▓│    /  .-"""""-.  \    │▓║▒│
║▓│    |  ( ( • ) )  |    │▓║▒│
║▓│    \  '-.....-'  /    │▓║▒│
║▓│     '-.........-'     │▓║▒│
║▓│                       │▓║▒│
║▓│  •   (▒▒▒▒▒▒▒▒▒)      │▓║▒╯
║▓└───────────────────────┘▓║╱
╚═══════════════════════════╝
  ▼                       ▼
'''.strip("\n")

# Shown instead of the above when the terminal is too short for them.
DEFAULT_SMALL_ART = r'''
┌─────────┐
│  ( • )  │
│ (( • )) │
│ ═══════ │
└─────────┘
'''.strip("\n")

# Mirrors that need their glyphs flipped too, not just reversed left-right.
MIRROR_TABLE = str.maketrans(
    "()[]{}/\\<>╭╮╰╯┌┐└┘╔╗╚╝╱╲",
    ")(][}{\\/><╮╭╯╰┐┌┘└╗╔╝╚╲╱",
)

PHI = 1.618033988749895


def pair_speaker(lines, width, min_gap=3):
    """Turn a single hand-drawn speaker into a symmetric left/right pair:
    [margin][speaker][gap][speaker][margin] inside `width`. The two
    speakers are fixed-width elements, so this isn't a golden-section
    *point* split -- the leftover slack (width minus both speakers) is
    itself divided margin : gap = 1 : phi, rather than evenly, which is
    what keeps the proportion "golden" instead of a plain centered grid.
    A gap sized to just fill the width regardless of the art's own size
    is what pushed a small icon implausibly far from its mirror before."""
    slot_w = max((len(line) for line in lines), default=0)
    slack = max(0, width - 2 * slot_w)
    margin = slack / (2 + PHI)
    left_pad = max(0, round(margin))
    gap = max(min_gap, round(PHI * margin))
    out = []
    for line in lines:
        mirrored = line.translate(MIRROR_TABLE)[::-1].center(slot_w)
        row = " " * left_pad + mirrored + " " * gap + line.center(slot_w)
        # Padded out to exactly `width` so render_speakers' own centering
        # (based on the longest paired line) is a no-op -- otherwise it
        # would re-center this already golden-positioned block again on
        # top, shifting the margins off their intended proportion.
        out.append(row.ljust(width))
    return out


def load_art(path, default):
    """Read a user-editable ascii-art file (creating it with a default the
    first time), trimmed of leading/trailing blank lines. Returns the
    single speaker as drawn; pair_speaker mirrors it at render time, since
    the gap depends on the terminal's current width."""
    if not os.path.exists(path):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write(default + "\n")
    with open(path) as f:
        lines = f.read().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines
