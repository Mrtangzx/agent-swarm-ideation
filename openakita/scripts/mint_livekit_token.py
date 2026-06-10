"""Mint a LiveKit access token for the local voice-room.

Usage:
    uv run python scripts/mint_livekit_token.py              # default: identity=user-1, room=voice-room, ttl=2h
    uv run python scripts/mint_livekit_token.py --identity alice --room my-room --ttl 3600

The token is what you paste into the test HTML page at livekit/web/index.html
or use with meet.livekit.io. It expires after ``--ttl`` seconds.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openakita.channels.livekit.agent_worker import _make_access_token  # noqa: E402

DEFAULT_API_KEY = "devkey"
DEFAULT_API_SECRET = "devsecret-change-me"
DEFAULT_ROOM = "voice-room"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--api-secret", default=DEFAULT_API_SECRET)
    p.add_argument("--identity", default="user-1")
    p.add_argument("--room", default=DEFAULT_ROOM)
    p.add_argument("--ttl", type=int, default=7200, help="token lifetime, seconds")
    args = p.parse_args()

    token = _make_access_token(
        api_key=args.api_key,
        api_secret=args.api_secret,
        identity=args.identity,
        room=args.room,
        ttl_seconds=args.ttl,
    )
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())