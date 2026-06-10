"""Serve livekit/web/ on http://localhost:8080/ for the demo HTML page.

This is a tiny static file server, intended for local development.
Run alongside scripts/echo_demo.py (or scripts/start_voice.py).
"""
from __future__ import annotations

import http.server
import socketserver
import sys
from pathlib import Path

PORT = 8080
WEB_DIR = Path(__file__).resolve().parents[1] / "livekit" / "web"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self) -> None:
        # Disable caching during dev so users always see the latest HTML.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[serve_test_page] " + (fmt % args) + "\n")


def main() -> int:
    if not WEB_DIR.is_dir():
        print(f"[error] web dir not found: {WEB_DIR}", file=sys.stderr)
        return 1
    # Force UTF-8 stdout for unicode-safe prints on Windows GBK consoles.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f">>> Test page served at http://localhost:{PORT}/  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())