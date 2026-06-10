"""Start OpenAkita with the LiveKit voice channel enabled.

This is the dev entry point for the voice channel. It:

1. Registers the LiveKit adapter with OpenAkita's adapter registry
   (so the gateway treats voice-room as a first-class channel alongside
   Feishu / Telegram / DingTalk / etc).
2. Adds a bot entry to the gateway's bot config pointing at the local
   LiveKit server.
3. Hands off to ``openakita serve``, which boots the FastAPI app on
   18900 (and runs the gateway + all registered adapters).

Prerequisites:
- LiveKit server running on ws://localhost:7880  (scripts/start_livekit_server.ps1)
- .env contains LITELLM_API_KEY (and any other model keys)
- An LLM endpoint configured under settings.data_dir

Environment overrides (all optional):
    LIVEKIT_URL             default ws://localhost:7880
    LIVEKIT_API_KEY         default devkey
    LIVEKIT_API_SECRET      default devsecret-change-me
    LIVEKIT_ROOM            default voice-room
    LIVEKIT_TTS_VOICE       default zh-CN-XiaoxiaoNeural
    OPENAKITA_PROFILE       default default (which AgentProfile to use)

Usage:
    uv run python scripts/start_voice.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Make `openakita` importable from src/ without an install step.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openakita.channels.livekit import register_livekit_adapter  # noqa: E402
from openakita.channels.registry import ADAPTER_REGISTRY  # noqa: E402

logger = logging.getLogger(__name__)


def _livekit_creds_from_env() -> dict:
    return {
        "livekit_url": os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        "livekit_api_key": os.getenv("LIVEKIT_API_KEY", "devkey"),
        "livekit_api_secret": os.getenv("LIVEKIT_API_SECRET", "devsecret-change-me"),
        "room": os.getenv("LIVEKIT_ROOM", "voice-room"),
        "tts_voice": os.getenv("LIVEKIT_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
    }


def _install_livekit_bot() -> None:
    """Register the livekit adapter and add a bot entry to runtime_state.

    OpenAkita's gateway reads ``data/runtime_state.json`` on serve startup
    for the list of IM bots to register. We append (or update) a livekit
    entry, then reload.
    """
    import json

    register_livekit_adapter()

    if "livekit" not in ADAPTER_REGISTRY:
        raise RuntimeError("livekit adapter did not register; aborting")

    from openakita.config import settings
    state_file = Path(settings.project_root) / "data" / "runtime_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    state.setdefault("im_bots", [])
    bots = state["im_bots"]
    # Replace existing livekit entry (idempotent).
    bots = [b for b in bots if b.get("type") != "livekit"]
    bots.append({
        "type": "livekit",
        "id": os.getenv("LIVEKIT_BOT_ID", "voice-1"),
        "agent_profile_id": os.getenv("OPENAKITA_PROFILE", "default"),
        "enabled": True,
        "credentials": _livekit_creds_from_env(),
    })
    state["im_bots"] = bots
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"LiveKit bot entry written to {state_file}")


def main() -> int:
    logging.basicConfig(
        level=os.getenv("OPENAKITA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_livekit_bot()
    # Hand off to OpenAkita's main entry.
    from openakita.main import app
    try:
        app()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())