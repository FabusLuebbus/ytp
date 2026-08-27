"""Command-line interface for ytp."""

from __future__ import annotations

import argparse

from . import __version__
from .app import run


def main() -> None:
    """Parse command-line arguments and launch the player."""
    parser = argparse.ArgumentParser(
        prog="ytp",
        description="A terminal YouTube audio player.",
    )
    parser.add_argument("query", nargs="?", help="YouTube URL or search string")
    parser.add_argument("--version", action="version", version=f"ytp {__version__}")
    args = parser.parse_args()
    if not args.query:
        run(None)
        return
    is_url = "://" in args.query or args.query.startswith(
        ("youtu.be/", "youtube.com/", "www.youtube.com/")
    )
    if is_url:
        run(args.query)
    else:
        run(None, initial_search=args.query)
