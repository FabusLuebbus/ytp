"""The main event loop: state, key handling, and frame assembly. Playback
itself lives in Mpv; data lookups in youtube.py/beat.py; drawing in
render.py."""

import os
import sys
import threading
import time
from dataclasses import dataclass, field

from blessed import Terminal

from .art import DEFAULT_ART, DEFAULT_MED_ART, DEFAULT_SMALL_ART, load_art, pair_speaker
from .beat import analyze_beat
from .config import ART_PATH, MED_ART_PATH, SMALL_ART_PATH
from .eq import EQ_ORDER, EQ_RANGE_DB, build_af, load_eq, save_eq
from .favorites import load_favorites, toggle_favorite
from .history import load_history, save_history
from .mpv import Mpv
from .render import (
    CLEAR_EOL,
    bold,
    clamp_scroll,
    dim,
    render_eq_curve,
    render_plain_list,
    render_progress_bar,
    render_speakers,
    render_table,
)
from .youtube import fetch_channel_videos, fetch_mix, fetch_search, fetch_video

BROWSE_LABELS = {"mix": "Recommended", "channel": "Same channel", "search": "Search results"}

HISTORY_LIMIT = 25  # how many previously played tracks to keep, for prev/next and the History view

HELP_TEXT = """ytp -- keyboard help  (h, q, enter, or esc to close)

Playback (always available)
  space        play / pause
  <- / ->      seek -15s / +15s
  ctrl+<- / -> previous / next track (also OS/hardware media keys)
  f            mark/unmark the current track (from the queue view)
  v            toggle visual: rainbow sweep / beat-synced pulse
  q            quit

Queue view (default)
  up / down    select track in queue
  enter        play selected track now (drops earlier queued tracks)
  x            remove selected track from queue
  b            switch to browse view
  F            open favorites (F again to close)
  H            open play history (H again to close)

Browse view (pick what to queue next, without interrupting playback)
  up / down    select track
  enter        add selected track to the queue
  c            more from the same channel as the current track
  m            back to YouTube's recommended mix
  /            new search
  b            back to queue view
  f            mark/unmark the selected track

Favorites view
  up / down    select favorite
  enter        play favorite now
  x            remove favorite
  b / F        back to queue view

History view
  up / down    select track
  enter        play track now
  b / H        back to queue view

Equalizer (e to open, from queue or browse)
  <- / ->      select bass / mid / treble
  up / down    raise / lower gain (dB)
  r            reset to flat

Other
  p            hide/show the panel below the progress bar
  h            toggle this help screen""".splitlines()


def run_async(fn, *args):
    """Run fn(*args) in a background thread, returning a dict the caller
    can poll: {"result": None, "done": False}. Used so a slow yt-dlp call
    never blocks the render loop or key handling."""
    state = {"result": None, "done": False}

    def worker():
        state["result"] = fn(*args)
        state["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return state


@dataclass
class PlayerState:
    """All player state that key handling and rendering read or mutate.
    Kept as one object (rather than a pile of nonlocals) so the key
    dispatch in handle_key() can be unit tested without a real terminal,
    mpv process, or network access."""

    current: dict
    play_queue: list = field(default_factory=list)
    queue_selected: int = 0
    queue_scroll: int = 0
    history: list = field(default_factory=list)
    history_selected: int = 0
    history_scroll: int = 0
    view: str = "queue"  # "queue" | "browse" | "favorites" | "history" | "eq"
    browse_source: str = "mix"  # "mix" | "channel" | "search"
    browse_items: list = field(default_factory=list)
    browse_selected: int = 0
    browse_scroll: int = 0
    browse_job: dict | None = None
    favorites: dict = field(default_factory=dict)
    search_buffer: str = ""
    typing: bool = False
    eq_gains: dict = field(default_factory=dict)
    eq_selected: int = 0
    visual_mode: str = "rainbow"  # "rainbow" (continuous sweep) or "beat" (pulses on the beat)
    beat_info: dict | None = None
    beat_job: dict | None = None
    next_beat_time: float | None = None
    panel_hidden: bool = False
    showing_help: bool = False
    cur_pl_index: int = 0  # index of `current` within mpv's own playlist


def resync_playlist(state, mpv):
    if not state.current["url"]:
        return
    prev_url = state.history[-1]["url"] if state.history else None
    next_url = state.play_queue[0]["url"] if state.play_queue else None
    mpv.sync_playlist(state.current["url"], prev_url, next_url)
    state.cur_pl_index = 1 if prev_url else 0


def remember(state, track):
    state.history.append(track)
    del state.history[:-HISTORY_LIMIT]
    save_history(state.history)


def start_track(state, mpv, track, clear_queue=False):
    """Make a browsed/favorite track the active track, including startup."""
    if state.current["url"]:
        remember(state, state.current)
    state.current = track
    if clear_queue:
        state.play_queue = []
        state.queue_selected = 0
    mpv.load(state.current["url"])
    state.beat_info = None
    state.next_beat_time = None
    state.beat_job = run_async(analyze_beat, state.current["url"])
    resync_playlist(state, mpv)


def advance(state, mpv, forward):
    """Move to the next/previous track (queue <-> history), for the
    Ctrl+Left/Right hotkeys and for playlist-pos-detected OS media-key
    Next/Previous alike. Returns False (no-op) if there's nowhere to
    go -- empty queue for forward, empty history for backward."""
    if not state.current["url"]:
        return False
    if forward:
        if not state.play_queue:
            return False
        remember(state, state.current)
        state.current = state.play_queue.pop(0)
    else:
        if not state.history:
            return False
        state.play_queue.insert(0, state.current)
        state.current = state.history.pop()
        save_history(state.history)
    state.queue_selected = 0
    state.beat_info = None
    state.next_beat_time = None
    state.beat_job = run_async(analyze_beat, state.current["url"])
    if state.browse_source == "mix":
        state.browse_selected = 0
        state.browse_job = run_async(fetch_mix, state.current["id"])
    resync_playlist(state, mpv)
    return True


def handle_help_key(key):
    """Dispatch for the full-screen help overlay. Returns "quit", "close",
    or None (stay open)."""
    if key.lower() == "q":
        return "quit"
    if key.lower() == "h" or key.name in ("KEY_ESCAPE", "KEY_ENTER") or key == "\x1b":
        return "close"
    return None


def handle_key(key, state, mpv):
    """Dispatch one keystroke against the player state, mutating it (and
    issuing the matching mpv command) in place. Returns True if the app
    should quit, else None."""
    if state.typing:
        if key.name == "KEY_ESCAPE" or key == "\x1b":
            state.typing = False
        elif key.name == "KEY_ENTER" or key in ("\n", "\r"):
            state.typing = False
            if state.search_buffer.strip():
                state.view = "browse"
                state.browse_source = "search"
                state.browse_items = []
                state.browse_selected = 0
                state.browse_job = run_async(fetch_search, state.search_buffer.strip())
        elif key.name == "KEY_BACKSPACE" or key in ("\x7f", "\x08"):
            state.search_buffer = state.search_buffer[:-1]
        elif not key.is_sequence and key.isprintable():
            state.search_buffer += str(key)
        return None

    if key == "h":
        state.showing_help = True
        return None

    if key.lower() == "v":
        state.visual_mode = "beat" if state.visual_mode == "rainbow" else "rainbow"
        return None

    if key.name == "KEY_CTRL_LEFT" or key in ("\x1b[1;5D", "\x1b[5D"):
        advance(state, mpv, forward=False)
        return None
    if key.name == "KEY_CTRL_RIGHT" or key in ("\x1b[1;5C", "\x1b[5C"):
        advance(state, mpv, forward=True)
        return None

    if state.view == "eq":
        name = EQ_ORDER[state.eq_selected]
        if key.name == "KEY_LEFT":
            state.eq_selected = (state.eq_selected - 1) % len(EQ_ORDER)
        elif key.name == "KEY_RIGHT":
            state.eq_selected = (state.eq_selected + 1) % len(EQ_ORDER)
        elif key.name == "KEY_UP":
            state.eq_gains[name] = min(EQ_RANGE_DB, state.eq_gains[name] + 1)
            mpv.set_af(build_af(state.eq_gains))
            save_eq(state.eq_gains)
        elif key.name == "KEY_DOWN":
            state.eq_gains[name] = max(-EQ_RANGE_DB, state.eq_gains[name] - 1)
            mpv.set_af(build_af(state.eq_gains))
            save_eq(state.eq_gains)
        elif key.lower() == "r":
            state.eq_gains = {n: 0.0 for n in EQ_ORDER}
            mpv.set_af(build_af(state.eq_gains))
            save_eq(state.eq_gains)
        elif key == " ":
            mpv.toggle_pause()
        elif key.lower() == "p":
            state.panel_hidden = not state.panel_hidden
        elif key.lower() in ("e", "b"):
            state.view = "queue"
        elif key.lower() == "q":
            return True
        return None

    if key == " ":
        mpv.toggle_pause()
    elif key.name == "KEY_LEFT":
        mpv.seek(-15)
    elif key.name == "KEY_RIGHT":
        mpv.seek(15)
    elif key.name == "KEY_UP":
        if state.view == "queue":
            state.queue_selected = max(0, state.queue_selected - 1)
        elif state.view == "history":
            state.history_selected = max(0, state.history_selected - 1)
        else:
            state.browse_selected = max(0, state.browse_selected - 1)
    elif key.name == "KEY_DOWN":
        if state.view == "queue":
            state.queue_selected = min(len(state.play_queue) - 1, state.queue_selected + 1)
        elif state.view == "favorites":
            state.browse_selected = min(len(state.favorites) - 1, state.browse_selected + 1)
        elif state.view == "history":
            state.history_selected = min(len(state.history) - 1, state.history_selected + 1)
        else:
            state.browse_selected = min(len(state.browse_items) - 1, state.browse_selected + 1)
    elif key.name == "KEY_ENTER" or key in ("\n", "\r"):
        if state.view == "queue" and state.play_queue:
            if state.queue_selected < len(state.play_queue):
                remember(state, state.current)
                state.current = state.play_queue[state.queue_selected]
                state.play_queue = state.play_queue[state.queue_selected + 1:]
                state.queue_selected = 0
                state.beat_info = None
                state.next_beat_time = None
                state.beat_job = run_async(analyze_beat, state.current["url"])
                if state.browse_source == "mix":
                    state.browse_selected = 0
                    state.browse_job = run_async(fetch_mix, state.current["id"])
                resync_playlist(state, mpv)
        elif state.view == "browse" and state.browse_items:
            chosen = state.browse_items[state.browse_selected]
            if not state.current["url"]:
                start_track(state, mpv, chosen)
            else:
                state.play_queue.append(chosen)
                resync_playlist(state, mpv)
        elif state.view == "favorites" and state.favorites:
            favorite_items = list(state.favorites.values())
            start_track(state, mpv, favorite_items[state.browse_selected], clear_queue=True)
        elif state.view == "history" and state.history:
            start_track(state, mpv, list(reversed(state.history))[state.history_selected], clear_queue=True)
    elif key.lower() == "x" and state.view == "queue" and state.play_queue:
        state.play_queue.pop(state.queue_selected)
        state.queue_selected = min(state.queue_selected, max(0, len(state.play_queue) - 1))
        resync_playlist(state, mpv)
    elif key.lower() == "x" and state.view == "favorites":
        favorite_items = list(state.favorites.values())
        if favorite_items:
            toggle_favorite(state.favorites, favorite_items[state.browse_selected])
            favorite_items = list(state.favorites.values())
            state.browse_selected = min(state.browse_selected, max(0, len(favorite_items) - 1))
    elif key.lower() == "b":
        state.view = "browse" if state.view == "queue" else "queue"
        if state.view == "browse" and not state.browse_items and state.browse_job is None:
            state.browse_job = run_async(fetch_mix, state.current["id"])
    elif key.lower() == "c":
        state.view = "browse"
        state.browse_source = "channel"
        state.browse_items = []
        state.browse_selected = 0
        state.browse_job = run_async(fetch_channel_videos, state.current.get("channel_url"), state.current["id"])
    elif key.lower() == "m":
        state.view = "browse"
        state.browse_source = "mix"
        state.browse_items = []
        state.browse_selected = 0
        state.browse_job = run_async(fetch_mix, state.current["id"])
    elif key == "/":
        state.view = "browse"
        state.typing = True
        state.search_buffer = ""
    elif key.lower() == "e":
        state.view = "eq"
        state.panel_hidden = False
    elif key == "F":
        if state.view == "favorites":
            state.view = "queue"
        else:
            state.view = "favorites"
            state.panel_hidden = False
            state.browse_selected = 0
            state.browse_scroll = 0
    elif key == "H":
        if state.view == "history":
            state.view = "queue"
        else:
            state.view = "history"
            state.panel_hidden = False
            state.history_selected = 0
            state.history_scroll = 0
    elif key.lower() == "f":
        if state.view == "browse":
            if state.browse_items:
                toggle_favorite(state.favorites, state.browse_items[state.browse_selected])
        elif state.view == "queue":
            toggle_favorite(state.favorites, state.current)
    elif key.lower() == "p":
        state.panel_hidden = not state.panel_hidden
    elif key.lower() == "q":
        return True
    return None


def run(url=None, initial_search=None):
    """Start the terminal player, optionally beginning with a URL or search."""
    term = Terminal()
    if url:
        print("Loading…")
        current = fetch_video(url)
    else:
        current = {
            "id": "",
            "title": "No track selected",
            "channel": "",
            "channel_url": None,
            "duration": None,
            "url": None,
        }

    history = load_history()  # previously played tracks, most recent last; for prev/next
    del history[:-HISTORY_LIMIT]

    state = PlayerState(
        current=current,
        history=history,
        browse_job=run_async(fetch_mix, current["id"]) if current["id"] else None,
        favorites=load_favorites(),
        search_buffer=initial_search or "",
        typing=initial_search is not None,
        eq_gains=load_eq(),
        beat_job=run_async(analyze_beat, current["url"]) if current["url"] else None,
    )

    art_tall = load_art(ART_PATH, DEFAULT_ART)
    art_tall_mtime = os.path.getmtime(ART_PATH)
    art_med = load_art(MED_ART_PATH, DEFAULT_MED_ART)
    art_med_mtime = os.path.getmtime(MED_ART_PATH)
    art_small = load_art(SMALL_ART_PATH, DEFAULT_SMALL_ART)
    art_small_mtime = os.path.getmtime(SMALL_ART_PATH)

    MIN_WIDTH = 20
    CORE_LINES = 4  # blank-after-art, title, progress bar, blank-after-bar

    mpv = Mpv(state.current["url"])
    mpv.set_af(build_af(state.eq_gains))

    beat_hue = 0.0
    beat_hue_target = 0.0
    beat_flash = 0.0

    resync_playlist(state, mpv)
    # Raw ANSI instead of term.fullscreen()/term.hidden_cursor(): those are
    # blessed capability lookups (smcup/rmcup, civis/cnorm) and, like
    # term.home/term.clear before them, can silently no-op on some
    # terminals. If the alternate-screen switch doesn't actually happen,
    # every frame renders onto the normal scrolling buffer instead -- a
    # frame even one line taller than the viewport then scrolls it, and
    # \x1b[H just moves to row 1 of whatever's now on screen, not a fixed
    # absolute position. That drift is what caused old content (e.g. a
    # previous view's legend line) to survive one row off from the new
    # frame's instead of being overwritten. \x1b[?1049h/l and \x1b[?25l/h
    # are near-universal DEC private modes with no such fallback risk.
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    sys.stdout.flush()
    try:
        with term.cbreak():
            t0 = time.time()
            prev_dims = None
            while True:
                if state.browse_job is not None and state.browse_job["done"]:
                    state.browse_items = state.browse_job["result"]
                    state.browse_selected = 0
                    state.browse_job = None

                if state.beat_job is not None and state.beat_job["done"]:
                    state.beat_info = state.beat_job["result"]
                    state.next_beat_time = None
                    state.beat_job = None

                try:
                    mtime = os.path.getmtime(ART_PATH)
                    if mtime != art_tall_mtime:
                        art_tall = load_art(ART_PATH, DEFAULT_ART)
                        art_tall_mtime = mtime
                    mtime = os.path.getmtime(MED_ART_PATH)
                    if mtime != art_med_mtime:
                        art_med = load_art(MED_ART_PATH, DEFAULT_MED_ART)
                        art_med_mtime = mtime
                    mtime = os.path.getmtime(SMALL_ART_PATH)
                    if mtime != art_small_mtime:
                        art_small = load_art(SMALL_ART_PATH, DEFAULT_SMALL_ART)
                        art_small_mtime = mtime
                except OSError:
                    pass

                # mpv's own playlist always mirrors [prev?, current, next?]
                # (see resync_playlist), so a track ending naturally, an OS
                # media-key Next/Previous (mpv-mpris runs playlist-next /
                # playlist-prev for those), or our own Ctrl+Left/Right all
                # show up the same way: playlist-pos moving away from
                # cur_pl_index. advance() re-syncs it back afterwards.
                pl_pos = mpv.get("playlist-pos")
                if pl_pos is not None and pl_pos != state.cur_pl_index:
                    advance(state, mpv, forward=pl_pos > state.cur_pl_index)

                width, height = term.width, term.height
                # Never render into the true last column: some terminals
                # render our double-width emoji icon (or other characters)
                # a column wider than expected, which pushes anything sized
                # to exactly `width` into a wrap -- garbling or dropping
                # the last character (seen as a cut-off progress-bar time
                # or a missing edge column on the right speaker). Content
                # sizing uses `w`; `width`/`height` stay for size checks.
                w = max(1, width - 1)

                # Rows are positioned by absolute row number (\x1b[N;1H)
                # rather than built as one long string relying on "\n" to
                # advance the cursor sequentially. A line that happens to
                # land exactly on the terminal's last column leaves the
                # cursor in a "pending wrap" state, and terminals disagree
                # on whether a following "\n" then advances one row or two
                # -- which showed up as an old row (e.g. a previous view's
                # legend line) surviving one row off from where the new
                # frame's content landed instead of being overwritten.
                # Absolute positioning sidesteps that ambiguity entirely:
                # each row is independent of whatever happened above it.
                screen_rows = []

                def put(text=""):
                    screen_rows.append(text)

                pre = "\x1b[2J" if (width, height) != prev_dims else ""
                prev_dims = (width, height)

                if state.showing_help:
                    for line in HELP_TEXT:
                        put((bold(line) if line and line[0] != " " else line)[:w])
                    frame = pre + "".join(f"\x1b[{i + 1};1H{r}{CLEAR_EOL}" for i, r in enumerate(screen_rows)) + "\x1b[J"
                    sys.stdout.write("\x1b[?2026h" + frame + "\x1b[?2026l")
                    sys.stdout.flush()
                    key = term.inkey(timeout=0.1)
                    if not key:
                        continue
                    result = handle_help_key(key)
                    if result == "quit":
                        break
                    if result == "close":
                        state.showing_help = False
                    continue

                # "Too small" means literally not enough room for the core
                # (the small art in full, plus title + progress bar); the
                # panel below (queue/browse/eq) is optional and just gets
                # hidden -- via 'p' or automatically -- once it doesn't fit.
                min_needed = len(art_small) + CORE_LINES
                if width < MIN_WIDTH or height < min_needed:
                    put("Terminal too small — resize to continue…"[:w])
                    frame = pre + "".join(f"\x1b[{i + 1};1H{r}{CLEAR_EOL}" for i, r in enumerate(screen_rows)) + "\x1b[J"
                    sys.stdout.write("\x1b[?2026h" + frame + "\x1b[?2026l")
                    sys.stdout.flush()
                    key = term.inkey(timeout=0.1)
                    if key and key.lower() == "q":
                        break
                    continue

                # Prefer the tallest art that plus the core (title +
                # progress bar) still fully fits, falling back tall -> med
                # -> small. Either way the chosen art is shown in full.
                if art_tall and len(art_tall) + CORE_LINES <= height:
                    art, visual_h = art_tall, len(art_tall)
                elif art_med and len(art_med) + CORE_LINES <= height:
                    art, visual_h = art_med, len(art_med)
                else:
                    art, visual_h = art_small, len(art_small)

                # EQ is a dedicated editing window. Give it at least half of
                # the terminal for the curve and controls, shrinking the
                # speaker art when the taller variants would crowd it out.
                if state.view == "eq":
                    eq_panel = (height + 1) // 2
                    max_eq_visual = height - CORE_LINES - eq_panel
                    for candidate in (art_tall, art_med, art_small):
                        if candidate and len(candidate) <= max_eq_visual:
                            art, visual_h = candidate, len(candidate)
                            break

                pos = mpv.get("time-pos")
                if state.visual_mode == "beat" and state.beat_info and pos is not None:
                    interval = state.beat_info["interval"]
                    while interval < 2.0:  # keep the pulse under 0.5 Hz so the
                        interval *= 2       # fade between pulses stays visible
                    # Resync if we're not tracking yet or drifted far (e.g.
                    # after a seek), otherwise just check for a beat crossing.
                    if state.next_beat_time is None or not (pos - interval < state.next_beat_time < pos + interval * 4):
                        phase = state.beat_info["phase"] % interval
                        state.next_beat_time = pos - (pos % interval) + phase
                        while state.next_beat_time < pos:
                            state.next_beat_time += interval
                    if pos >= state.next_beat_time:
                        beat_hue_target = (beat_hue_target + 0.11) % 1.0
                        beat_flash = 1.0
                        state.next_beat_time += interval
                beat_flash *= 0.85
                # Ease the displayed hue towards the current target every
                # frame (shortest way around the color wheel), instead of
                # snapping straight to it -- so the color keeps fading right
                # up until the next beat picks a new target, not just the
                # brightness.
                hue_diff = ((beat_hue_target - beat_hue + 0.5) % 1.0) - 0.5
                beat_hue = (beat_hue + hue_diff * 0.12) % 1.0

                paired_art = pair_speaker(art, w)
                if state.visual_mode == "beat":
                    speaker_lines = render_speakers(paired_art, w, visual_h, 0, beat_hue=beat_hue, beat_flash=beat_flash)
                else:
                    speaker_lines = render_speakers(paired_art, w, visual_h, time.time() - t0)
                for line in speaker_lines:
                    put(line)
                put()

                title_line = f"{state.current['title']} — {state.current['channel']}"
                if state.current.get("id") in state.favorites:
                    title_line = "★ " + title_line
                put(bold(title_line[:w]))

                dur = mpv.get("duration") or state.current.get("duration")
                paused = bool(mpv.get("pause"))
                bar = render_progress_bar(w, pos, dur, paused)
                put(bar or dim("(too narrow for a progress bar)"))
                put()

                # The panel (queue/browse/eq + legend) only gets whatever
                # room is left after the core; below one line it's just not
                # shown, and 'p' lets you hide it by choice too.
                panel_avail = max(0, height - visual_h - CORE_LINES)
                list_h = 0
                if state.panel_hidden:
                    if panel_avail >= 1:
                        put(dim("(panel hidden — press p to show)"[:w]))
                elif panel_avail < 1:
                    pass
                elif state.typing:
                    legend = f"Search: {state.search_buffer}_"[:w]
                    put(bold(legend))
                    empty_msg = None
                    items, selected = [], 0
                    list_h = panel_avail - 1
                elif state.view == "queue":
                    pos_hint = f" [{state.queue_selected + 1}/{len(state.play_queue)}]" if state.play_queue else ""
                    legend = f"Queue{pos_hint}  (↑↓ select · ↵ play now · f favorite current · x remove · b browse · p hide · space pause · q quit)"[:w]
                    put(bold(legend))
                    items, selected = state.play_queue, state.queue_selected
                    empty_msg = "(queue is empty — press b to browse and add tracks)"
                    list_h = panel_avail - 1
                elif state.view == "eq":
                    eq_name = EQ_ORDER[state.eq_selected]
                    legend = f"EQ: {eq_name.upper()} {state.eq_gains[eq_name]:+.0f} dB  (←→ select · ↑↓ adjust · r reset · e/b back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = None, None
                    empty_msg = None
                    list_h = panel_avail - 1
                    if list_h > 0:
                        for line in render_eq_curve(state.eq_gains, eq_name, w, list_h)[:list_h]:
                            put(line)
                elif state.view == "favorites":
                    favorite_items = list(state.favorites.values())
                    pos_hint = f" [{state.browse_selected + 1}/{len(favorite_items)}]" if favorite_items else ""
                    legend = f"Favorites{pos_hint}  (↑↓ select · ↵ play now · x remove · b/F back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = favorite_items, state.browse_selected
                    list_h = panel_avail - 1
                    empty_msg = "(no favorites yet — press f on a track to add one)"
                    if list_h > 0 and items:
                        visible_rows = max(1, list_h - 4)
                        state.browse_scroll = clamp_scroll(state.browse_scroll, selected, len(items), visible_rows)
                        table = render_table(items, selected, w, list_h, state.browse_scroll, set(state.favorites))
                        table_rows = table or render_plain_list(items, selected, w, list_h, state.browse_scroll, set(state.favorites))
                        for line in table_rows[:list_h]:
                            put(line)
                    elif not items:
                        put(dim(empty_msg))
                elif state.view == "history":
                    history_items = list(reversed(state.history))
                    pos_hint = f" [{state.history_selected + 1}/{len(history_items)}]" if history_items else ""
                    legend = f"History{pos_hint}  (↑↓ select · ↵ play now · b/H back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = history_items, state.history_selected
                    list_h = panel_avail - 1
                    empty_msg = "(no playback history yet)"
                    if list_h > 0 and items:
                        visible_rows = max(1, list_h - 4)
                        state.history_scroll = clamp_scroll(state.history_scroll, selected, len(items), visible_rows)
                        table = render_table(items, selected, w, list_h, state.history_scroll, set(state.favorites))
                        table_rows = table or render_plain_list(items, selected, w, list_h, state.history_scroll, set(state.favorites))
                        for line in table_rows[:list_h]:
                            put(line)
                    elif not items:
                        put(dim(empty_msg))
                else:
                    label = BROWSE_LABELS[state.browse_source]
                    pos_hint = f" [{state.browse_selected + 1}/{len(state.browse_items)}]" if state.browse_items else ""
                    legend = f"Browse: {label}{pos_hint}  (↑↓ select · ↵ queue · c channel · m mix · / search · b back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = state.browse_items, state.browse_selected
                    list_h = panel_avail - 1
                    if state.browse_job is not None:
                        empty_msg = "(loading…)"
                    elif state.browse_source == "channel":
                        empty_msg = "(no videos found for this channel)"
                    elif state.browse_source == "search":
                        empty_msg = "(press / to search)"
                    else:
                        empty_msg = "(loading…)"

                if state.view in ("queue", "browse") and not state.typing and not state.panel_hidden and panel_avail >= 1:
                    if not items:
                        put(dim(empty_msg))
                    elif list_h > 0:
                        # Table rows cost 4 extra lines for borders/header;
                        # the plain-list fallback doesn't, but using the
                        # same (slightly conservative) visible-row count
                        # for the scroll math either way is harmless -- at
                        # most a couple of extra already-fitting rows show
                        # in the fallback case.
                        visible_rows = max(1, list_h - 4)
                        if state.view == "queue":
                            state.queue_scroll = clamp_scroll(state.queue_scroll, selected, len(items), visible_rows)
                            offset = state.queue_scroll
                        else:
                            state.browse_scroll = clamp_scroll(state.browse_scroll, selected, len(items), visible_rows)
                            offset = state.browse_scroll
                        table = render_table(items, selected, w, list_h, offset, set(state.favorites))
                        table_rows = table if table else render_plain_list(items, selected, w, list_h, offset, set(state.favorites))
                        for line in table_rows[:list_h]:
                            put(line)

                frame = pre + "".join(f"\x1b[{i + 1};1H{r}{CLEAR_EOL}" for i, r in enumerate(screen_rows)) + "\x1b[J"
                # "Synchronized update" (DEC mode 2026): terminals that
                # support it paint the whole frame atomically instead of
                # showing the individual per-row cursor jumps as they
                # arrive; terminals that don't know the mode just ignore it.
                sys.stdout.write("\x1b[?2026h" + frame + "\x1b[?2026l")
                sys.stdout.flush()

                key = term.inkey(timeout=0.1)
                if not key:
                    continue

                if handle_key(key, state, mpv):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        mpv.quit()
