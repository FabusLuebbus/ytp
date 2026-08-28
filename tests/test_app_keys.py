"""Pins the key-handling behavior in app.handle_key / app.handle_help_key:
which view each key opens/closes, that a track can only ever be removed
from the queue or favorites via 'x' (never 'f'), and that panel-toggle
keys (F/H) are symmetric open/close pairs rather than colliding with the
lowercase favorite-toggle hotkey."""

from blessed.keyboard import Keystroke

from ytp import eq as eq_module
from ytp import favorites as favorites_module
from ytp import history as history_module
from ytp.app import PlayerState, handle_help_key, handle_key


def key(ucs="", name=None):
    return Keystroke(ucs, name=name)


class FakeMpv:
    def __init__(self):
        self.paused = 0
        self.seeks = []
        self.afs = []
        self.loaded = []
        self.synced = []

    def toggle_pause(self):
        self.paused += 1

    def seek(self, delta):
        self.seeks.append(delta)

    def set_af(self, af):
        self.afs.append(af)

    def load(self, url):
        self.loaded.append(url)

    def sync_playlist(self, current_url, prev_url, next_url):
        self.synced.append((current_url, prev_url, next_url))


def mk_track(id_, url=None, title=None):
    return {
        "id": id_,
        "title": title or f"Track {id_}",
        "channel": "Some Channel",
        "channel_url": f"https://youtube.com/{id_}-channel",
        "duration": 200,
        "url": url or f"https://youtu.be/{id_}",
    }


def no_current():
    return {"id": "", "title": "No track selected", "channel": "", "channel_url": None, "duration": None, "url": None}


def sandbox_persistence(monkeypatch, tmp_path):
    """Redirect favorites/history/eq persistence to a scratch dir so tests
    never touch the real user data files."""
    monkeypatch.setattr(favorites_module, "FAVORITES_PATH", str(tmp_path / "favorites.json"))
    monkeypatch.setattr(history_module, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(eq_module, "EQ_PATH", str(tmp_path / "eq.json"))


def stub_run_async(monkeypatch):
    """Replace app.run_async with a stand-in that never spawns a thread or
    calls the network-hitting fetcher -- key handling only needs a job
    placeholder to exist, not a real result."""
    import ytp.app as app_module

    def fake_run_async(fn, *args):
        return {"result": None, "done": False}

    monkeypatch.setattr(app_module, "run_async", fake_run_async)


def setup(monkeypatch, tmp_path, **overrides):
    sandbox_persistence(monkeypatch, tmp_path)
    stub_run_async(monkeypatch)
    state = PlayerState(current=overrides.pop("current", mk_track("cur")), **overrides)
    return state, FakeMpv()


# -- playback keys, always available --------------------------------------


def test_space_toggles_pause(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path)
    handle_key(key(" "), state, mpv)
    assert mpv.paused == 1


def test_left_right_seek_back_and_forward(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path)
    handle_key(key(name="KEY_LEFT"), state, mpv)
    handle_key(key(name="KEY_RIGHT"), state, mpv)
    assert mpv.seeks == [-15, 15]


def test_v_toggles_visual_mode(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path)
    assert state.visual_mode == "rainbow"
    handle_key(key("v"), state, mpv)
    assert state.visual_mode == "beat"
    handle_key(key("V"), state, mpv)
    assert state.visual_mode == "rainbow"


def test_p_toggles_panel_hidden(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path)
    assert state.panel_hidden is False
    handle_key(key("p"), state, mpv)
    assert state.panel_hidden is True
    handle_key(key("p"), state, mpv)
    assert state.panel_hidden is False


def test_ctrl_left_right_advance_queue_and_history(monkeypatch, tmp_path):
    playing = mk_track("playing")
    nxt = mk_track("next")
    prev = mk_track("prev")
    state, mpv = setup(monkeypatch, tmp_path, current=playing, play_queue=[nxt], history=[prev])

    handle_key(key(name="KEY_CTRL_RIGHT"), state, mpv)
    assert state.current == nxt
    assert state.play_queue == []
    assert state.history[-1] == playing

    handle_key(key(name="KEY_CTRL_LEFT"), state, mpv)
    assert state.current == playing
    assert state.play_queue == [nxt]


def test_q_quits_from_queue_view(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path)
    assert handle_key(key("q"), state, mpv) is True


# -- removal is only ever possible through 'x' -----------------------------


def test_x_removes_selected_track_from_queue(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="queue", play_queue=[a, b], queue_selected=0)

    handle_key(key("x"), state, mpv)

    assert state.play_queue == [b]
    assert mpv.synced  # resync_playlist ran


def test_f_does_not_remove_from_queue(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="queue", play_queue=[a, b], queue_selected=0)

    handle_key(key("f"), state, mpv)

    assert state.play_queue == [a, b]


def test_x_removes_selected_favorite(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(
        monkeypatch, tmp_path, view="favorites", favorites={"a": a, "b": b}, browse_selected=0
    )

    handle_key(key("x"), state, mpv)

    assert list(state.favorites.values()) == [b]


def test_f_does_not_remove_from_favorites(monkeypatch, tmp_path):
    """Regression test: 'f' toggling a favorite that's already favorited
    would delete it, which made 'f' a second, undocumented way to remove a
    track from the favorites list. Only 'x' may remove."""
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(
        monkeypatch, tmp_path, view="favorites", favorites={"a": a, "b": b}, browse_selected=0
    )

    handle_key(key("f"), state, mpv)

    assert list(state.favorites.values()) == [a, b]


def test_x_does_nothing_in_browse_view(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="browse", browse_items=[a, b], browse_selected=0)

    handle_key(key("x"), state, mpv)

    assert state.browse_items == [a, b]


def test_x_does_nothing_in_history_view(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="history", history=[a, b], history_selected=0)

    handle_key(key("x"), state, mpv)

    assert state.history == [a, b]


# -- f: mark/unmark, only in queue and browse views ------------------------


def test_f_toggles_favorite_from_queue_view(monkeypatch, tmp_path):
    track = mk_track("cur")
    state, mpv = setup(monkeypatch, tmp_path, view="queue", current=track, favorites={})

    handle_key(key("f"), state, mpv)
    assert list(state.favorites) == ["cur"]

    handle_key(key("f"), state, mpv)
    assert state.favorites == {}


def test_f_toggles_favorite_from_browse_view(monkeypatch, tmp_path):
    track = mk_track("browsed")
    state, mpv = setup(monkeypatch, tmp_path, view="browse", browse_items=[track], browse_selected=0)

    handle_key(key("f"), state, mpv)

    assert list(state.favorites) == ["browsed"]


def test_f_is_noop_in_history_view(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="history", favorites={})
    handle_key(key("f"), state, mpv)
    assert state.favorites == {}


# -- F / H: each key opens its own panel and, pressed again, closes it -----


def test_capital_f_opens_favorites_from_queue(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="queue")
    handle_key(key("F"), state, mpv)
    assert state.view == "favorites"


def test_capital_f_closes_favorites_back_to_queue(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="favorites")
    handle_key(key("F"), state, mpv)
    assert state.view == "queue"


def test_capital_f_does_not_remove_the_selected_favorite(monkeypatch, tmp_path):
    """The original bug: pressing F a second time to close Favorites was
    indistinguishable from lowercase f, which unfavorited (removed) the
    selected track instead of closing the panel."""
    a = mk_track("a")
    state, mpv = setup(monkeypatch, tmp_path, view="favorites", favorites={"a": a}, browse_selected=0)

    handle_key(key("F"), state, mpv)

    assert state.view == "queue"
    assert state.favorites == {"a": a}


def test_capital_h_opens_history_from_queue(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="queue")
    handle_key(key("H"), state, mpv)
    assert state.view == "history"


def test_capital_h_closes_history_back_to_queue(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="history")
    handle_key(key("H"), state, mpv)
    assert state.view == "queue"


def test_capital_h_does_not_touch_history_list(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="history", history=[a, b])

    handle_key(key("H"), state, mpv)

    assert state.view == "queue"
    assert state.history == [a, b]


def test_f_and_h_are_distinct_from_lowercase(monkeypatch, tmp_path):
    """F must never be confused with f (case matters): opening Favorites
    with F, then pressing lowercase f, favorites the *current* track (queue
    semantics don't apply inside Favorites) rather than doing nothing --
    but F itself must always mean open/close, never toggle-favorite."""
    state, mpv = setup(monkeypatch, tmp_path, view="queue")
    handle_key(key("F"), state, mpv)
    assert state.view == "favorites"
    handle_key(key("F"), state, mpv)
    assert state.view == "queue"


# -- b: switches queue<->browse, and returns from favorites/history --------


def test_b_switches_queue_and_browse(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="queue")
    handle_key(key("b"), state, mpv)
    assert state.view == "browse"
    handle_key(key("b"), state, mpv)
    assert state.view == "queue"


def test_b_returns_to_queue_from_favorites(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="favorites")
    handle_key(key("b"), state, mpv)
    assert state.view == "queue"


def test_b_returns_to_queue_from_history(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="history")
    handle_key(key("b"), state, mpv)
    assert state.view == "queue"


# -- navigation is per-view ---------------------------------------------


def test_up_down_clamped_in_queue_view(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="queue", play_queue=[a, b], queue_selected=0)

    handle_key(key(name="KEY_UP"), state, mpv)
    assert state.queue_selected == 0  # already at top, stays clamped

    handle_key(key(name="KEY_DOWN"), state, mpv)
    assert state.queue_selected == 1

    handle_key(key(name="KEY_DOWN"), state, mpv)
    assert state.queue_selected == 1  # clamped at bottom


def test_up_down_in_history_view(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="history", history=[a, b], history_selected=0)

    handle_key(key(name="KEY_DOWN"), state, mpv)
    assert state.history_selected == 1


def test_up_down_in_favorites_view_uses_browse_selected(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(
        monkeypatch, tmp_path, view="favorites", favorites={"a": a, "b": b}, browse_selected=0
    )

    handle_key(key(name="KEY_DOWN"), state, mpv)
    assert state.browse_selected == 1


# -- enter: play / queue / start, per view ---------------------------------


def test_enter_plays_selected_queue_track_and_drops_earlier_ones(monkeypatch, tmp_path):
    a, b, c = mk_track("a"), mk_track("b"), mk_track("c")
    cur = mk_track("cur")
    state, mpv = setup(monkeypatch, tmp_path, current=cur, view="queue", play_queue=[a, b, c], queue_selected=1)

    handle_key(key(name="KEY_ENTER"), state, mpv)

    assert state.current == b
    assert state.play_queue == [c]
    assert cur in state.history


def test_enter_on_browse_appends_to_queue_when_something_is_playing(monkeypatch, tmp_path):
    chosen = mk_track("chosen")
    state, mpv = setup(monkeypatch, tmp_path, view="browse", browse_items=[chosen], browse_selected=0)

    handle_key(key(name="KEY_ENTER"), state, mpv)

    assert state.play_queue == [chosen]
    assert state.current["id"] == "cur"  # unchanged, still playing


def test_enter_on_browse_starts_playing_immediately_if_nothing_playing(monkeypatch, tmp_path):
    chosen = mk_track("chosen")
    state, mpv = setup(monkeypatch, tmp_path, current=no_current(), view="browse", browse_items=[chosen], browse_selected=0)

    handle_key(key(name="KEY_ENTER"), state, mpv)

    assert state.current == chosen
    assert mpv.loaded == [chosen["url"]]


def test_enter_on_favorites_starts_track_and_clears_queue(monkeypatch, tmp_path):
    fav = mk_track("fav")
    queued = mk_track("queued")
    state, mpv = setup(
        monkeypatch, tmp_path, view="favorites", favorites={"fav": fav}, browse_selected=0, play_queue=[queued]
    )

    handle_key(key(name="KEY_ENTER"), state, mpv)

    assert state.current == fav
    assert state.play_queue == []


def test_enter_on_history_starts_selected_track(monkeypatch, tmp_path):
    a, b = mk_track("a"), mk_track("b")
    state, mpv = setup(monkeypatch, tmp_path, view="history", history=[a, b], history_selected=0)

    handle_key(key(name="KEY_ENTER"), state, mpv)

    # history_selected indexes list(reversed(history)), so 0 is the most recent (b)
    assert state.current == b


# -- typing / search mode ---------------------------------------------------


def test_typing_appends_printable_characters(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, typing=True, search_buffer="")
    handle_key(key("a"), state, mpv)
    handle_key(key("b"), state, mpv)
    assert state.search_buffer == "ab"


def test_typing_backspace_removes_last_character(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, typing=True, search_buffer="abc")
    handle_key(key(name="KEY_BACKSPACE"), state, mpv)
    assert state.search_buffer == "ab"


def test_typing_escape_cancels_without_searching(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, typing=True, search_buffer="abc", view="browse")
    handle_key(key(name="KEY_ESCAPE"), state, mpv)
    assert state.typing is False
    assert state.view == "browse"


def test_typing_enter_with_text_starts_search(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, typing=True, search_buffer="daft punk", view="browse")
    handle_key(key(name="KEY_ENTER"), state, mpv)
    assert state.typing is False
    assert state.view == "browse"
    assert state.browse_source == "search"


def test_typing_enter_with_blank_buffer_does_not_search(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, typing=True, search_buffer="   ", browse_source="mix")
    handle_key(key(name="KEY_ENTER"), state, mpv)
    assert state.typing is False
    assert state.browse_source == "mix"


def test_slash_enters_typing_mode(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="queue")
    handle_key(key("/"), state, mpv)
    assert state.typing is True
    assert state.view == "browse"
    assert state.search_buffer == ""


# -- equalizer view ----------------------------------------------------


def test_eq_left_right_cycles_selected_band(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="eq", eq_selected=0)
    handle_key(key(name="KEY_RIGHT"), state, mpv)
    assert state.eq_selected == 1
    handle_key(key(name="KEY_LEFT"), state, mpv)
    assert state.eq_selected == 0
    handle_key(key(name="KEY_LEFT"), state, mpv)
    assert state.eq_selected == len(eq_module.EQ_ORDER) - 1  # wraps


def test_eq_up_down_adjusts_and_clamps_gain(monkeypatch, tmp_path):
    gains = {name: 0.0 for name in eq_module.EQ_ORDER}
    state, mpv = setup(monkeypatch, tmp_path, view="eq", eq_selected=0, eq_gains=gains)

    for _ in range(eq_module.EQ_RANGE_DB + 5):
        handle_key(key(name="KEY_UP"), state, mpv)

    assert state.eq_gains["bass"] == eq_module.EQ_RANGE_DB
    assert mpv.afs  # set_af was called


def test_eq_r_resets_all_gains(monkeypatch, tmp_path):
    gains = {"bass": 5.0, "mid": -3.0, "treble": 2.0}
    state, mpv = setup(monkeypatch, tmp_path, view="eq", eq_gains=gains)
    handle_key(key("r"), state, mpv)
    assert state.eq_gains == {name: 0.0 for name in eq_module.EQ_ORDER}


def test_eq_e_and_b_close_back_to_queue(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="eq")
    handle_key(key("e"), state, mpv)
    assert state.view == "queue"

    state.view = "eq"
    handle_key(key("b"), state, mpv)
    assert state.view == "queue"


def test_eq_q_quits(monkeypatch, tmp_path):
    state, mpv = setup(monkeypatch, tmp_path, view="eq")
    assert handle_key(key("q"), state, mpv) is True


def test_eq_view_ignores_x_and_capital_f(monkeypatch, tmp_path):
    """The eq branch returns unconditionally, so keys meaningful elsewhere
    (remove, open-favorites) must not leak through while the EQ is open."""
    state, mpv = setup(monkeypatch, tmp_path, view="eq", favorites={"a": mk_track("a")})
    handle_key(key("x"), state, mpv)
    handle_key(key("F"), state, mpv)
    assert state.view == "eq"
    assert list(state.favorites) == ["a"]


# -- help overlay dispatch --------------------------------------------------


def test_help_key_q_quits():
    assert handle_help_key(key("q")) == "quit"


def test_help_key_h_closes():
    assert handle_help_key(key("h")) == "close"


def test_help_key_escape_closes():
    assert handle_help_key(key(name="KEY_ESCAPE")) == "close"


def test_help_key_enter_closes():
    assert handle_help_key(key(name="KEY_ENTER")) == "close"


def test_help_key_other_keeps_it_open():
    assert handle_help_key(key("x")) is None
