import sys

from ytp import cli


def test_url_is_played_directly(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run", calls.append)
    monkeypatch.setattr(sys, "argv", ["ytp", "https://www.youtube.com/watch?v=abc"])

    cli.main()

    assert calls == ["https://www.youtube.com/watch?v=abc"]


def test_plain_argument_seeds_tui_search(monkeypatch):
    calls = []
    def record(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(cli, "run", record)
    monkeypatch.setattr(sys, "argv", ["ytp", "artist song"])

    cli.main()

    assert calls == [((None,), {"initial_search": "artist song"})]


def test_no_argument_opens_empty_tui_search(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run", calls.append)
    monkeypatch.setattr(sys, "argv", ["ytp"])

    cli.main()

    assert calls == [None]
