"""IPC wrapper around an mpv subprocess used purely as an audio backend."""

import json
import os
import socket
import subprocess
import tempfile
import time

from .config import MPRIS_SCRIPT


class Mpv:
    def __init__(self, url):
        self.sock = os.path.join(tempfile.gettempdir(), f"ytp-mpv-{os.getpid()}.sock")
        cmd = [
            "mpv", "--no-video", "--idle=yes", "--no-terminal",
            "--really-quiet", "--ytdl-format=bestaudio",
            f"--input-ipc-server={self.sock}",
        ]
        if MPRIS_SCRIPT and os.path.exists(MPRIS_SCRIPT):
            cmd.append(f"--script={MPRIS_SCRIPT}")
        if url:
            cmd.append(url)
        self.proc = subprocess.Popen(cmd)
        for _ in range(100):
            if os.path.exists(self.sock):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("mpv IPC socket never appeared")
        time.sleep(0.2)

    def _cmd(self, command):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(self.sock)
                s.sendall((json.dumps({"command": command}) + "\n").encode())
                buf = b""
                while True:
                    while b"\n" not in buf:
                        chunk = s.recv(4096)
                        if not chunk:
                            return None
                        buf += chunk
                    line, _, buf = buf.partition(b"\n")
                    msg = json.loads(line)
                    if "event" not in msg:
                        # mpv can interleave unsolicited event notifications
                        # (e.g. from the MPRIS plugin acting on an OS media
                        # key) with our command reply on the same socket;
                        # skip those and keep waiting for our own reply.
                        return msg
        except (OSError, ValueError):
            return None

    def get(self, prop):
        r = self._cmd(["get_property", prop])
        return r.get("data") if r else None

    def load(self, url):
        self._cmd(["loadfile", url, "replace"])

    def sync_playlist(self, current_url, prev_url, next_url):
        """Rebuild mpv's playlist as [prev?, current, next?] so that OS
        media-key / MPRIS Next and Previous -- which mpv-mpris implements
        as the playlist-next / playlist-prev commands -- have something to
        actually act on, and so mpv auto-advances on its own when a track
        ends naturally (the render loop just polls playlist-pos to notice
        either kind of change).

        Called after every queue edit, not just an actual track change --
        so if `current_url` is already the entry playing, it's left alone
        (no loadfile replace) and only the other slots are touched, or
        this would restart the current track from 0 every time."""
        playlist = self.get("playlist") or []
        cur_idx = next((i for i, e in enumerate(playlist) if e.get("current")), None)
        if cur_idx is None or playlist[cur_idx].get("filename") != current_url:
            self._cmd(["loadfile", current_url, "replace"])
            playlist = [{"filename": current_url}]
            cur_idx = 0
        for i in reversed(range(len(playlist))):
            if i != cur_idx:
                self._cmd(["playlist-remove", i])
        if next_url:
            self._cmd(["loadfile", next_url, "append"])
        if prev_url:
            self._cmd(["loadfile", prev_url, "insert-at", 0])

    def toggle_pause(self):
        self._cmd(["cycle", "pause"])

    def set_af(self, af):
        self._cmd(["set_property", "af", af])

    def seek(self, offset):
        self._cmd(["seek", offset, "relative"])

    def quit(self):
        self._cmd(["quit"])
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        try:
            os.remove(self.sock)
        except OSError:
            pass
