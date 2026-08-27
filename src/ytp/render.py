"""Terminal rendering: colors, the animated speaker visual, the progress
bar, bordered tables, and the EQ curve."""

import colorsys
import math

from .eq import EQ_KEYPOINT_HZ, EQ_RANGE_DB

# Raw ANSI SGR codes instead of blessed's capability lookups (term.bold,
# term.dim, ...): some terminfo entries (tmux/screen) don't define every
# capability blessed expects and raise TypeError deep in curses.tparm.
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
HIGHLIGHT = "\x1b[30;46m"  # black on cyan
CLEAR_EOL = "\x1b[K"

# Same box-drawing style as the `haspkg` shell function.
BORDER = "\x1b[38;2;108;112;134m"
HEAD = "\x1b[1;38;2;203;166;247m"
CYAN = "\x1b[38;2;137;220;235m"  # the actively selected EQ keypoint


def bold(s):
    return f"{BOLD}{s}{RESET}"


def dim(s):
    return f"{DIM}{s}{RESET}"


def highlight(s):
    return f"{HIGHLIGHT}{s}{RESET}"


def fmt_time(seconds):
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_speakers(art, width, height, t, beat_hue=None, beat_flash=0.0):
    """Center `art` (already trimmed lines) in a width x height box.

    In the default "rainbow" mode (beat_hue=None) it's colored with a
    continuous, slowly shifting hue sweep. In "beat" mode the hue instead
    jumps in fixed steps on each detected beat and holds between beats,
    while brightness flashes up on the beat and decays -- a color-change +
    fade + pulsate effect driven by the actual beat, not just a clock.

    The left (mirrored) and right (as-drawn) speaker sweep in opposite
    directions and carry a fixed hue offset from each other, so the pair
    doesn't just look like one image stamped twice.
    """
    art_h = len(art)
    art_w = max((len(line) for line in art), default=0)
    left = max(0, (width - art_w) // 2)
    top = max(0, (height - art_h) // 2)
    mid = left + art_w / 2
    PHASE_OFFSET = 0.5
    lines = []
    for row in range(height):
        art_row = row - top
        src = art[art_row].center(art_w) if 0 <= art_row < art_h else ""
        line = " " * left + src
        out = []
        for col, ch in enumerate(line[:width]):
            if ch == " ":
                out.append(" ")
                continue
            is_right = col >= mid
            if beat_hue is None:
                # Hue is anchored to each speaker's OWN local position, 0
                # at its inner edge (nearest the terminal's middle) to 1 at
                # its own outer edge -- using the raw column in the full
                # terminal width instead made the two speakers naturally
                # land in different hue ranges just from sitting at
                # different columns, and adding the +0.5 phase offset on
                # top of that wrapped it right back into nearly the same
                # range as the other speaker, canceling out the intended
                # opposite-color effect. Both sides use the same "-t" sign
                # against this inner-to-outer axis, so a given hue's
                # position moves from the middle towards its own outer
                # edge over time -- the same motion mirrored, so in screen
                # space the two speakers visibly animate apart from the
                # center rather than in the same direction.
                if is_right:
                    local_frac = (col - mid) / max(width - mid, 1)
                    phase = PHASE_OFFSET
                else:
                    local_frac = (mid - col) / max(mid - left, 1)
                    phase = 0.0
                hue = (local_frac - t * 0.15 + phase) % 1.0
                val = 1.0
            else:
                # Spread across each speaker's OWN span (not the full,
                # gap-including width) so each one shows a real multicolor
                # gradient rather than one near-solid color -- col/width
                # only crawled a small fraction of the wheel across a
                # single narrow speaker.
                if is_right:
                    local_frac = (col - mid) / max(width - mid, 1)
                    base = (beat_hue + PHASE_OFFSET) % 1.0
                    spread = -0.7
                else:
                    local_frac = (col - left) / max(mid - left, 1)
                    base = beat_hue
                    spread = 0.7
                hue = (base + spread * local_frac) % 1.0
                # Opposite pulse phase: one speaker brightens on the beat
                # while the other dims, instead of both flashing in unison.
                val = (1.0 - 0.5 * beat_flash) if is_right else (0.5 + 0.5 * beat_flash)
            r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.85, val))
            out.append(f"\x1b[38;2;{r};{g};{b}m{ch}")
        lines.append("".join(out) + "\x1b[0m")
    return lines


def render_progress_bar(width, pos, dur, paused):
    """Returns one line that always fits exactly in `width` columns, or
    None if there isn't room for a bar at all (caller just omits it)."""
    pos = pos or 0
    dur = dur or 0
    icon = "⏸️" if paused else "▶️"
    icon_w = 2  # emoji render as double-width in practically every terminal
    times = f"{fmt_time(pos)} / {fmt_time(dur)}"
    overhead = icon_w + 1 + 2 + 1 + len(times)  # icon + " [" + bar + "] " + times
    inner = width - overhead
    if inner < 4:
        return None
    frac = 0 if dur <= 0 else max(0, min(1, pos / dur))
    filled = round(frac * inner)
    bar = []
    for col in range(inner):
        if col < filled - 1:
            ch = "="
        elif col == filled - 1:
            ch = ">"
        else:
            ch = "-"
        if ch in "=>":
            hue = 0.33 - 0.33 * (col / max(inner - 1, 1))  # green -> red
            r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.9, 0.9))
            bar.append(f"\x1b[38;2;{r};{g};{b}m{ch}")
        else:
            bar.append(f"\x1b[38;2;90;90;90m{ch}")
    return f"{icon} [" + "".join(bar) + f"\x1b[0m] {times}"


def clamp_scroll(offset, selected, total, visible):
    """Slide the visible window just enough to keep `selected` in view
    (not recentering every time), so arrowing past the top/bottom edge of
    a short list scrolls to the next/previous page of items."""
    if total <= visible:
        return 0
    if selected < offset:
        offset = selected
    elif selected >= offset + visible:
        offset = selected - visible + 1
    return max(0, min(offset, total - visible))


def render_table(items, selected, width, max_rows, offset=0, favorite_ids=None):
    """A bordered ╭─┬─╮ table like haspkg's, showing items[offset:], with
    `selected` (an index into the full `items`) highlighted if it's in
    that visible slice. None tells the caller to fall back to a plain
    list instead."""
    duration_w, channel_w, min_title_w = 8, 20, 20
    overhead = 4 + 6  # 4 vertical borders + 2 padding spaces per column
    avail_title = width - duration_w - channel_w - overhead
    rows_avail = max_rows - 4  # top border, header, header separator, bottom border
    if avail_title < min_title_w or rows_avail < 1:
        return None
    title_w = min(60, avail_title)

    def border_row(left, mid, right):
        def seg(w):
            return "─" * (w + 2)
        return f"{BORDER}{left}{seg(title_w)}{mid}{seg(duration_w)}{mid}{seg(channel_w)}{right}{RESET}"

    lines = [border_row("╭", "┬", "╮")]
    lines.append(
        f"{BORDER}│{RESET} {HEAD}{'TITLE':<{title_w}}{RESET} "
        f"{BORDER}│{RESET} {HEAD}{'DURATION':<{duration_w}}{RESET} "
        f"{BORDER}│{RESET} {HEAD}{'CHANNEL':<{channel_w}}{RESET} {BORDER}│{RESET}"
    )
    lines.append(border_row("├", "┼", "┤"))
    for i, item in enumerate(items[offset:offset + rows_avail]):
        marker = "★ " if favorite_ids and item.get("id") in favorite_ids else "  "
        title = (marker + item["title"])[:title_w].ljust(title_w)
        dur = fmt_time(item["duration"]).rjust(duration_w)
        chan = item["channel"][:channel_w].ljust(channel_w)
        if i == selected - offset:
            lines.append(highlight(f"│ {title} │ {dur} │ {chan} │"))
        else:
            lines.append(
                f"{BORDER}│{RESET} {title} {BORDER}│{RESET} {dur} {BORDER}│{RESET} {chan} {BORDER}│{RESET}"
            )
    lines.append(border_row("╰", "┴", "╯"))
    return lines


def render_plain_list(items, selected, width, max_rows, offset=0, favorite_ids=None):
    lines = []
    for i, item in enumerate(items[offset:offset + max_rows]):
        marker = "★ " if favorite_ids and item.get("id") in favorite_ids else "  "
        line = f"{(marker + item['title'])[:60]:60}  {fmt_time(item['duration']):>8}  {item['channel'][:20]}"
        lines.append(highlight(line[:width]) if i == selected - offset else line[:width])
    return lines


# Braille cells give 2x4 sub-character resolution per terminal cell, which
# is what makes the EQ curve look like a smooth line instead of a blocky
# bar chart. Bit values are the standard Unicode braille dot-pattern
# numbering: dots 1-3 are the left column top-to-bottom, 4-6 the right
# column, 7-8 the bottom-left/bottom-right pair.
_BRAILLE_BIT = {
    (0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
    (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80,
}
_EQ_FREQ_LO = 20.0
_EQ_FREQ_HI = 20000.0


def _eq_curve_gain_fn(gains):
    """A smooth (cosine-eased) curve through the bass/mid/treble keypoints
    in log-frequency space, flat beyond the outermost ones -- a
    continuous stand-in for the discrete shelving/peaking filter that
    build_af actually applies."""
    points = sorted(
        ((math.log2(hz), gains[name]) for name, hz in EQ_KEYPOINT_HZ.items()),
        key=lambda p: p[0],
    )

    def gain_at(lf):
        if lf <= points[0][0]:
            return points[0][1]
        if lf >= points[-1][0]:
            return points[-1][1]
        for (l0, g0), (l1, g1) in zip(points, points[1:]):
            if l0 <= lf <= l1:
                t = 0 if l1 == l0 else (lf - l0) / (l1 - l0)
                eased = (1 - math.cos(t * math.pi)) / 2  # smooth S-curve, not a kink
                return g0 + (g1 - g0) * eased
        return points[-1][1]

    return gain_at


def render_eq_curve(gains, selected_name, width, height):
    """A continuous ascii curve (via braille sub-cells) through the
    bass/mid/treble gains, filled from the 0 dB baseline, plus a bottom
    row labeling the three keypoints."""
    if height < 2 or width < 4:
        return []
    char_rows = height - 1  # last row is keypoint labels
    if char_rows < 1:
        return []
    char_cols = width
    sub_w, sub_h = char_cols * 2, char_rows * 4

    lo_l, hi_l = math.log2(_EQ_FREQ_LO), math.log2(_EQ_FREQ_HI)
    gain_at = _eq_curve_gain_fn(gains)
    zero_row = (sub_h - 1) / 2

    def row_for_gain(g):
        return (EQ_RANGE_DB - g) / (2 * EQ_RANGE_DB) * (sub_h - 1)

    edge_rows = []
    for sx in range(sub_w):
        lf = lo_l + (hi_l - lo_l) * (sx + 0.5) / sub_w
        edge_rows.append(row_for_gain(gain_at(lf)))

    target_hz = EQ_KEYPOINT_HZ[selected_name]
    target_sx = round((math.log2(target_hz) - lo_l) / (hi_l - lo_l) * sub_w)

    grid = [[False] * sub_w for _ in range(sub_h)]
    for sx in range(sub_w):
        lo, hi = sorted((edge_rows[sx], zero_row))
        for sy in range(round(lo), round(hi) + 1):
            if 0 <= sy < sub_h:
                grid[sy][sx] = True

    def col_color(cx):
        lf = lo_l + (hi_l - lo_l) * (cx * 2 + 1) / sub_w
        if abs(cx * 2 - target_sx) <= 1:
            return CYAN
        g = gain_at(lf)
        hue = 0.33 if g >= 0 else 0.0  # green for boost, red for cut
        shade = 0.35 + 0.55 * min(1.0, abs(g) / EQ_RANGE_DB)
        r, gg, b = (round(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.85, shade))
        return f"\x1b[38;2;{r};{gg};{b}m"

    lines = []
    for cy in range(char_rows):
        cells = []
        for cx in range(char_cols):
            bits = 0
            for dx in range(2):
                for dy in range(4):
                    if grid[cy * 4 + dy][cx * 2 + dx]:
                        bits |= _BRAILLE_BIT[(dx, dy)]
            if bits == 0:
                cells.append(" ")
            else:
                cells.append(f"{col_color(cx)}{chr(0x2800 + bits)}")
        lines.append("".join(cells) + RESET)

    label_row = [" "] * width
    for name, hz in EQ_KEYPOINT_HZ.items():
        sx = round((math.log2(hz) - lo_l) / (hi_l - lo_l) * sub_w)
        cx = min(width - 1, sx // 2)
        text = f"{hz // 1000}k" if hz >= 1000 else str(hz)
        start = max(0, min(width - len(text), cx - len(text) // 2))
        styled = bold(f"{CYAN}{text}") if name == selected_name else dim(text)
        label_row[start:start + len(text)] = [styled] + [""] * (len(text) - 1)
    lines.append("".join(label_row))
    return lines
