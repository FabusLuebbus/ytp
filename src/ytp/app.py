"""The main event loop: state, key handling, and frame assembly. Playback
itself lives in Mpv; data lookups in youtube.py/beat.py; drawing in
render.py."""

import os
import sys
import threading
import time

from blessed import Terminal

from .art import DEFAULT_ART, DEFAULT_MED_ART, DEFAULT_SMALL_ART, load_art, pair_speaker
from .config import ART_PATH, MED_ART_PATH, SMALL_ART_PATH
from .eq import EQ_ORDER, EQ_RANGE_DB, build_af, load_eq, save_eq
from .favorites import load_favorites, toggle_favorite
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
  F            open favorites

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
  x / f        remove favorite

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

    play_queue = []
    queue_selected = 0
    queue_scroll = 0
    history = []  # previously played tracks, most recent last; for prev/next

    view = "queue"  # "queue" | "browse" | "favorites"
    browse_source = "mix"  # "mix" | "channel" | "search"
    browse_items = []
    browse_selected = 0
    browse_scroll = 0
    browse_job = run_async(fetch_mix, current["id"]) if current["id"] else None
    favorites = load_favorites()

    search_buffer = initial_search or ""
    typing = initial_search is not None

    eq_gains = load_eq()
    eq_selected = 0

    visual_mode = "rainbow"  # "rainbow" (continuous sweep) or "beat" (pulses on the beat)
    beat_info = None
    from .beat import analyze_beat
    beat_job = run_async(analyze_beat, current["url"]) if current["url"] else None
    next_beat_time = None
    beat_hue = 0.0
    beat_hue_target = 0.0
    beat_flash = 0.0

    art_tall = load_art(ART_PATH, DEFAULT_ART)
    art_tall_mtime = os.path.getmtime(ART_PATH)
    art_med = load_art(MED_ART_PATH, DEFAULT_MED_ART)
    art_med_mtime = os.path.getmtime(MED_ART_PATH)
    art_small = load_art(SMALL_ART_PATH, DEFAULT_SMALL_ART)
    art_small_mtime = os.path.getmtime(SMALL_ART_PATH)

    panel_hidden = False
    showing_help = False

    MIN_WIDTH = 20
    CORE_LINES = 4  # blank-after-art, title, progress bar, blank-after-bar

    mpv = Mpv(current["url"])
    mpv.set_af(build_af(eq_gains))

    cur_pl_index = 0  # index of `current` within mpv's own playlist

    def resync_playlist():
        nonlocal cur_pl_index
        if not current["url"]:
            return
        prev_url = history[-1]["url"] if history else None
        next_url = play_queue[0]["url"] if play_queue else None
        mpv.sync_playlist(current["url"], prev_url, next_url)
        cur_pl_index = 1 if prev_url else 0

    def start_track(track, clear_queue=False):
        """Make a browsed/favorite track the active track, including startup."""
        nonlocal current, play_queue, queue_selected, beat_info, next_beat_time, beat_job
        if current["url"]:
            history.append(current)
            del history[:-20]
        current = track
        if clear_queue:
            play_queue = []
            queue_selected = 0
        mpv.load(current["url"])
        beat_info = None
        next_beat_time = None
        beat_job = run_async(analyze_beat, current["url"])
        resync_playlist()

    def advance(forward):
        """Move to the next/previous track (queue <-> history), for the
        Ctrl+Left/Right hotkeys and for playlist-pos-detected OS media-key
        Next/Previous alike. Returns False (no-op) if there's nowhere to
        go -- empty queue for forward, empty history for backward."""
        nonlocal current, browse_selected, browse_job, beat_info, next_beat_time, beat_job, queue_selected
        if not current["url"]:
            return False
        if forward:
            if not play_queue:
                return False
            history.append(current)
            del history[:-20]
            current = play_queue.pop(0)
        else:
            if not history:
                return False
            play_queue.insert(0, current)
            current = history.pop()
        queue_selected = 0
        beat_info = None
        next_beat_time = None
        beat_job = run_async(analyze_beat, current["url"])
        if browse_source == "mix":
            browse_selected = 0
            browse_job = run_async(fetch_mix, current["id"])
        resync_playlist()
        return True

    resync_playlist()
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
                if browse_job is not None and browse_job["done"]:
                    browse_items = browse_job["result"]
                    browse_selected = 0
                    browse_job = None

                if beat_job is not None and beat_job["done"]:
                    beat_info = beat_job["result"]
                    next_beat_time = None
                    beat_job = None

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
                if pl_pos is not None and pl_pos != cur_pl_index:
                    advance(forward=pl_pos > cur_pl_index)

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

                if showing_help:
                    for line in HELP_TEXT:
                        put((bold(line) if line and line[0] != " " else line)[:w])
                    frame = pre + "".join(f"\x1b[{i + 1};1H{r}{CLEAR_EOL}" for i, r in enumerate(screen_rows)) + "\x1b[J"
                    sys.stdout.write("\x1b[?2026h" + frame + "\x1b[?2026l")
                    sys.stdout.flush()
                    key = term.inkey(timeout=0.1)
                    if not key:
                        continue
                    if key.lower() == "q":
                        break
                    elif key.lower() == "h" or key.name in ("KEY_ESCAPE", "KEY_ENTER") or key == "\x1b":
                        showing_help = False
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
                if view == "eq":
                    eq_panel = (height + 1) // 2
                    max_eq_visual = height - CORE_LINES - eq_panel
                    for candidate in (art_tall, art_med, art_small):
                        if candidate and len(candidate) <= max_eq_visual:
                            art, visual_h = candidate, len(candidate)
                            break

                pos = mpv.get("time-pos")
                if visual_mode == "beat" and beat_info and pos is not None:
                    interval = beat_info["interval"]
                    while interval < 2.0:  # keep the pulse under 0.5 Hz so the
                        interval *= 2       # fade between pulses stays visible
                    # Resync if we're not tracking yet or drifted far (e.g.
                    # after a seek), otherwise just check for a beat crossing.
                    if next_beat_time is None or not (pos - interval < next_beat_time < pos + interval * 4):
                        phase = beat_info["phase"] % interval
                        next_beat_time = pos - (pos % interval) + phase
                        while next_beat_time < pos:
                            next_beat_time += interval
                    if pos >= next_beat_time:
                        beat_hue_target = (beat_hue_target + 0.11) % 1.0
                        beat_flash = 1.0
                        next_beat_time += interval
                beat_flash *= 0.85
                # Ease the displayed hue towards the current target every
                # frame (shortest way around the color wheel), instead of
                # snapping straight to it -- so the color keeps fading right
                # up until the next beat picks a new target, not just the
                # brightness.
                hue_diff = ((beat_hue_target - beat_hue + 0.5) % 1.0) - 0.5
                beat_hue = (beat_hue + hue_diff * 0.12) % 1.0

                paired_art = pair_speaker(art, w)
                if visual_mode == "beat":
                    speaker_lines = render_speakers(paired_art, w, visual_h, 0, beat_hue=beat_hue, beat_flash=beat_flash)
                else:
                    speaker_lines = render_speakers(paired_art, w, visual_h, time.time() - t0)
                for line in speaker_lines:
                    put(line)
                put()

                title_line = f"{current['title']} — {current['channel']}"
                if current.get("id") in favorites:
                    title_line = "★ " + title_line
                put(bold(title_line[:w]))

                dur = mpv.get("duration") or current.get("duration")
                paused = bool(mpv.get("pause"))
                bar = render_progress_bar(w, pos, dur, paused)
                put(bar or dim("(too narrow for a progress bar)"))
                put()

                # The panel (queue/browse/eq + legend) only gets whatever
                # room is left after the core; below one line it's just not
                # shown, and 'p' lets you hide it by choice too.
                panel_avail = max(0, height - visual_h - CORE_LINES)
                list_h = 0
                if panel_hidden:
                    if panel_avail >= 1:
                        put(dim("(panel hidden — press p to show)"[:w]))
                elif panel_avail < 1:
                    pass
                elif typing:
                    legend = f"Search: {search_buffer}_"[:w]
                    put(bold(legend))
                    empty_msg = None
                    items, selected = [], 0
                    list_h = panel_avail - 1
                elif view == "queue":
                    pos_hint = f" [{queue_selected + 1}/{len(play_queue)}]" if play_queue else ""
                    legend = f"Queue{pos_hint}  (↑↓ select · ↵ play now · f favorite current · x remove · b browse · p hide · space pause · q quit)"[:w]
                    put(bold(legend))
                    items, selected = play_queue, queue_selected
                    empty_msg = "(queue is empty — press b to browse and add tracks)"
                    list_h = panel_avail - 1
                elif view == "eq":
                    eq_name = EQ_ORDER[eq_selected]
                    legend = f"EQ: {eq_name.upper()} {eq_gains[eq_name]:+.0f} dB  (←→ select · ↑↓ adjust · r reset · e/b back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = None, None
                    empty_msg = None
                    list_h = panel_avail - 1
                    if list_h > 0:
                        for line in render_eq_curve(eq_gains, eq_name, w, list_h)[:list_h]:
                            put(line)
                elif view == "favorites":
                    favorite_items = list(favorites.values())
                    pos_hint = f" [{browse_selected + 1}/{len(favorite_items)}]" if favorite_items else ""
                    legend = f"Favorites{pos_hint}  (↑↓ select · ↵ play now · x/f remove · b back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = favorite_items, browse_selected
                    list_h = panel_avail - 1
                    empty_msg = "(no favorites yet — press f on a track to add one)"
                    if list_h > 0 and items:
                        visible_rows = max(1, list_h - 4)
                        browse_scroll = clamp_scroll(browse_scroll, selected, len(items), visible_rows)
                        table = render_table(items, selected, w, list_h, browse_scroll, set(favorites))
                        table_rows = table or render_plain_list(items, selected, w, list_h, browse_scroll, set(favorites))
                        for line in table_rows[:list_h]:
                            put(line)
                    elif not items:
                        put(dim(empty_msg))
                else:
                    label = BROWSE_LABELS[browse_source]
                    pos_hint = f" [{browse_selected + 1}/{len(browse_items)}]" if browse_items else ""
                    legend = f"Browse: {label}{pos_hint}  (↑↓ select · ↵ queue · c channel · m mix · / search · b back · q quit)"[:w]
                    put(bold(legend))
                    items, selected = browse_items, browse_selected
                    list_h = panel_avail - 1
                    if browse_job is not None:
                        empty_msg = "(loading…)"
                    elif browse_source == "channel":
                        empty_msg = "(no videos found for this channel)"
                    elif browse_source == "search":
                        empty_msg = "(press / to search)"
                    else:
                        empty_msg = "(loading…)"

                if view in ("queue", "browse") and not typing and not panel_hidden and panel_avail >= 1:
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
                        if view == "queue":
                            queue_scroll = clamp_scroll(queue_scroll, selected, len(items), visible_rows)
                            offset = queue_scroll
                        else:
                            browse_scroll = clamp_scroll(browse_scroll, selected, len(items), visible_rows)
                            offset = browse_scroll
                        table = render_table(items, selected, w, list_h, offset, set(favorites))
                        table_rows = table if table else render_plain_list(items, selected, w, list_h, offset, set(favorites))
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

                if typing:
                    if key.name == "KEY_ESCAPE" or key == "\x1b":
                        typing = False
                    elif key.name == "KEY_ENTER" or key in ("\n", "\r"):
                        typing = False
                        if search_buffer.strip():
                            view = "browse"
                            browse_source = "search"
                            browse_items = []
                            browse_selected = 0
                            browse_job = run_async(fetch_search, search_buffer.strip())
                    elif key.name == "KEY_BACKSPACE" or key in ("\x7f", "\x08"):
                        search_buffer = search_buffer[:-1]
                    elif not key.is_sequence and key.isprintable():
                        search_buffer += str(key)
                    continue

                if key.lower() == "h":
                    showing_help = True
                    continue

                if key.lower() == "v":
                    visual_mode = "beat" if visual_mode == "rainbow" else "rainbow"
                    continue

                if key.name == "KEY_CTRL_LEFT" or key in ("\x1b[1;5D", "\x1b[5D"):
                    advance(forward=False)
                    continue
                if key.name == "KEY_CTRL_RIGHT" or key in ("\x1b[1;5C", "\x1b[5C"):
                    advance(forward=True)
                    continue

                if view == "eq":
                    name = EQ_ORDER[eq_selected]
                    if key.name == "KEY_LEFT":
                        eq_selected = (eq_selected - 1) % len(EQ_ORDER)
                    elif key.name == "KEY_RIGHT":
                        eq_selected = (eq_selected + 1) % len(EQ_ORDER)
                    elif key.name == "KEY_UP":
                        eq_gains[name] = min(EQ_RANGE_DB, eq_gains[name] + 1)
                        mpv.set_af(build_af(eq_gains))
                        save_eq(eq_gains)
                    elif key.name == "KEY_DOWN":
                        eq_gains[name] = max(-EQ_RANGE_DB, eq_gains[name] - 1)
                        mpv.set_af(build_af(eq_gains))
                        save_eq(eq_gains)
                    elif key.lower() == "r":
                        eq_gains = {n: 0.0 for n in EQ_ORDER}
                        mpv.set_af(build_af(eq_gains))
                        save_eq(eq_gains)
                    elif key == " ":
                        mpv.toggle_pause()
                    elif key.lower() == "p":
                        panel_hidden = not panel_hidden
                    elif key.lower() in ("e", "b"):
                        view = "queue"
                    elif key.lower() == "q":
                        break
                    continue

                if view == "favorites" and key.lower() in ("f", "x"):
                    favorite_items = list(favorites.values())
                    if favorite_items:
                        toggle_favorite(favorites, favorite_items[browse_selected])
                        favorite_items = list(favorites.values())
                        browse_selected = min(browse_selected, max(0, len(favorite_items) - 1))
                    continue

                if key == " ":
                    mpv.toggle_pause()
                elif key.name == "KEY_LEFT":
                    mpv.seek(-15)
                elif key.name == "KEY_RIGHT":
                    mpv.seek(15)
                elif key.name == "KEY_UP":
                    if view == "queue":
                        queue_selected = max(0, queue_selected - 1)
                    else:
                        browse_selected = max(0, browse_selected - 1)
                elif key.name == "KEY_DOWN":
                    if view == "queue":
                        queue_selected = min(len(play_queue) - 1, queue_selected + 1)
                    elif view == "favorites":
                        browse_selected = min(len(favorites) - 1, browse_selected + 1)
                    else:
                        browse_selected = min(len(browse_items) - 1, browse_selected + 1)
                elif key.name == "KEY_ENTER" or key in ("\n", "\r"):
                    if view == "queue" and play_queue:
                        if queue_selected < len(play_queue):
                            history.append(current)
                            del history[:-20]
                            current = play_queue[queue_selected]
                            play_queue = play_queue[queue_selected + 1:]
                            queue_selected = 0
                            beat_info = None
                            next_beat_time = None
                            beat_job = run_async(analyze_beat, current["url"])
                            if browse_source == "mix":
                                browse_selected = 0
                                browse_job = run_async(fetch_mix, current["id"])
                            resync_playlist()
                    elif view == "browse" and browse_items:
                        chosen = browse_items[browse_selected]
                        if not current["url"]:
                            start_track(chosen)
                        else:
                            play_queue.append(chosen)
                            resync_playlist()
                    elif view == "favorites" and favorites:
                        favorite_items = list(favorites.values())
                        start_track(favorite_items[browse_selected], clear_queue=True)
                elif key.lower() == "x" and view == "queue" and play_queue:
                    play_queue.pop(queue_selected)
                    queue_selected = min(queue_selected, max(0, len(play_queue) - 1))
                    resync_playlist()
                elif key.lower() == "b":
                    view = "browse" if view == "queue" else "queue"
                    if view == "browse" and not browse_items and browse_job is None:
                        browse_job = run_async(fetch_mix, current["id"])
                elif key.lower() == "c":
                    view = "browse"
                    browse_source = "channel"
                    browse_items = []
                    browse_selected = 0
                    browse_job = run_async(fetch_channel_videos, current.get("channel_url"), current["id"])
                elif key.lower() == "m":
                    view = "browse"
                    browse_source = "mix"
                    browse_items = []
                    browse_selected = 0
                    browse_job = run_async(fetch_mix, current["id"])
                elif key == "/":
                    view = "browse"
                    typing = True
                    search_buffer = ""
                elif key.lower() == "e":
                    view = "eq"
                    panel_hidden = False
                elif key == "F":
                    view = "favorites"
                    panel_hidden = False
                    browse_selected = 0
                    browse_scroll = 0
                elif key.lower() == "f":
                    if view == "browse":
                        if browse_items:
                            toggle_favorite(favorites, browse_items[browse_selected])
                    elif view == "queue":
                        toggle_favorite(favorites, current)
                elif key.lower() == "p":
                    panel_hidden = not panel_hidden
                elif key.lower() == "q":
                    break
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        mpv.quit()
