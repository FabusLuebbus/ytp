import json
import os
import socket
import tempfile
import threading

from ytp.mpv import Mpv


def watch(events):
    """Run Mpv._watch_events against a fake mpv socket that emits `events`
    and then closes; returns the Mpv-like object afterwards."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "mpv.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.recv(4096)  # the request_log_messages command
                for e in events:
                    conn.sendall((json.dumps(e) + "\n").encode())

        t = threading.Thread(target=serve)
        t.start()
        mpv = Mpv.__new__(Mpv)
        mpv.sock = path
        mpv.load_error = None
        mpv._watch_events()
        t.join()
        server.close()
        return mpv


def log(prefix, text):
    return {"event": "log-message", "prefix": prefix, "level": "warn", "text": text}


def test_failed_load_reports_first_logged_cause():
    mpv = watch([
        {"event": "start-file"},
        log("ffmpeg", "https: HTTP error 403 Forbidden\n"),
        log("cplayer", "No video or audio streams selected.\n"),
        {"event": "end-file", "reason": "error", "file_error": "no audio or video data played"},
    ])
    assert mpv.load_error == "ffmpeg: https: HTTP error 403 Forbidden"


def test_failed_load_without_log_falls_back_to_file_error():
    mpv = watch([
        {"event": "start-file"},
        {"event": "end-file", "reason": "error", "file_error": "loading failed"},
    ])
    assert mpv.load_error == "loading failed"


def test_next_start_file_clears_error():
    mpv = watch([
        {"event": "start-file"},
        {"event": "end-file", "reason": "error", "file_error": "loading failed"},
        {"event": "start-file"},
    ])
    assert mpv.load_error is None


def test_normal_end_of_file_is_not_an_error():
    mpv = watch([
        {"event": "start-file"},
        log("ffmpeg", "something harmless\n"),
        {"event": "end-file", "reason": "eof"},
    ])
    assert mpv.load_error is None
