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
    parser.add_argument("url", help="YouTube video or playlist URL")
    parser.add_argument("--version", action="version", version=f"ytp {__version__}")
    args = parser.parse_args()
    run(args.url)
