from ytp.eq import EQ_ORDER, EQ_RANGE_DB, build_af, interpolate_eq
from ytp.render import render_eq_curve


def test_eq_curve_hits_each_anchor() -> None:
    gains = {"bass": 4.0, "mid": -2.0, "treble": 7.0}

    bands = interpolate_eq(gains)

    assert bands[1000] == -2.0
    assert bands[8000] == 7.0


def test_eq_filter_contains_all_controls() -> None:
    gains = dict.fromkeys(EQ_ORDER, float(EQ_RANGE_DB))

    audio_filter = build_af(gains)

    assert "bass=g=12.00" in audio_filter
    assert "equalizer=f=1000" in audio_filter
    assert "treble=g=12.00" in audio_filter


def test_eq_preview_is_a_fitted_multiline_curve() -> None:
    lines = render_eq_curve({"bass": 5.0, "mid": -6.0, "treble": -3.0}, "mid", 48, 8)

    assert len(lines) == 8
    assert any("\u2800" <= char <= "\u28ff" for line in lines[:-1] for char in line)
