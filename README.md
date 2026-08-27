# ytp

A keyboard-driven terminal YouTube audio player with a queue, browse/search views,
animated ASCII speakers, and a three-band EQ.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `mpv`, `yt-dlp`, and `aubio` available on `PATH`

`aubio` is only needed for beat-synced speaker animation; playback works without it.

## Run

```sh
uv run ytp 'https://www.youtube.com/watch?v=...'
```

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
