"""TTS helpers for the LiveKit voice channel.

Reuses OpenAkita's avatar-studio Edge TTS implementation. avatar-studio
is a plugin (loaded by the plugin manager, not on the Python path), so
we load the file directly via importlib.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)


def _load_avatar_tts_edge() -> ModuleType:
    """Load avatar-studio's TTS module from the plugins directory."""
    plugin_file = (
        Path(__file__).resolve().parents[4]
        / "plugins"
        / "avatar-studio"
        / "avatar_tts_edge.py"
    )
    if not plugin_file.exists():
        raise RuntimeError(
            f"avatar-studio plugin not found at {plugin_file}. "
            "Install avatar-studio or override openakita.channels.livekit.tts."
        )
    spec = importlib.util.spec_from_file_location("avatar_tts_edge", plugin_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load spec for {plugin_file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Lazy attribute-style access: tests can patch ``openakita.channels.livekit.tts._avatar``
_avatar = None


def _get_avatar():
    global _avatar
    if _avatar is None:
        _avatar = _load_avatar_tts_edge()
    return _avatar


def synth_voice(text: str, voice: str, output_path, *, speed: float = 1.0, retry_count: int = 3):
    """Proxy to avatar-studio's async synth_voice."""
    return _get_avatar().synth_voice(
        text, voice, output_path, speed=speed, retry_count=retry_count
    )


def EDGE_VOICES():
    return list(_get_avatar().EDGE_VOICES)


def EDGE_VOICES_BY_ID():
    return dict(_get_avatar().EDGE_VOICES_BY_ID)


def list_voices() -> list[dict]:
    """Convenience for CLI / setup UI."""
    return list(_get_avatar().EDGE_VOICES)


def default_voice() -> str:
    """Pick a sensible Chinese female voice."""
    return "zh-CN-XiaoxiaoNeural"


__all__ = ["synth_voice", "list_voices", "default_voice"]