from ytp import history


def test_history_persists_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    monkeypatch.setattr(history, "HISTORY_PATH", str(path))
    tracks = [{"id": "a", "title": "Song A"}, {"id": "b", "title": "Song B"}]

    assert history.load_history() == []
    history.save_history(tracks)
    assert history.load_history() == tracks
