# ytp

A keyboard-driven terminal YouTube audio player with a queue, browse/search views,
animated ASCII speakers, and a three-band EQ.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `mpv`, `yt-dlp`, and `aubio` available on `PATH`

`aubio` is only needed for beat-synced speaker animation; playback works without it.

## Usage

```sh
uv run ytp                              # open an empty search window
uv run ytp "artist song"                 # open search with text prefilled
uv run ytp 'https://www.youtube.com/watch?v=...'  # play a URL directly
```

With no argument, type a query and press Enter. A quoted argument is placed
into the same search field; press Enter to load its results. Select a result
with the arrow keys and press Enter to add it to the queue, or—when starting
without a URL—make it the first playing track. Press `/` from browse view to
search again.

Key controls: `Space` pauses, `←`/`→` seek, `Ctrl-←`/`Ctrl-→` change tracks,
`b` switches between queue and browse, `e` opens the EQ, `f` marks the current
track in queue view (or the selected browse track), `F` opens Favorites, and
`q` quits. In Favorites, `Enter`
plays a track and `f` or `x` removes it. Favorites are stored in
`config/favorites.json` (or the directory selected by `YTP_CONFIG_DIR`).

The editable defaults live in [`config/`](config/): `eq.json` and the three speaker
art files. Set `YTP_CONFIG_DIR` to use a different directory; ytp never writes to
`~/.config/ytp`.

## Development

```sh
uv sync --group dev
uv run ruff check .
uv run pytest
uv run pip-audit
```
