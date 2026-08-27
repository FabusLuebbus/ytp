from ytp import favorites


def test_toggle_favorite_persists_and_removes(tmp_path, monkeypatch):
    path = tmp_path / "favorites.json"
    monkeypatch.setattr(favorites, "FAVORITES_PATH", str(path))
    track = {"id": "abc", "title": "Song", "url": "https://youtu.be/abc"}
    saved = {}

    assert favorites.toggle_favorite(saved, track) is True
    assert favorites.load_favorites() == {"abc": track}
    assert favorites.toggle_favorite(saved, track) is False
    assert favorites.load_favorites() == {}
