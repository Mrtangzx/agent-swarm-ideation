"""LiveKit voice channel — real-time bidirectional voice over WebRTC.

This package adds OpenAkita its first voice channel: a WebRTC bridge that
joins a LiveKit room, transcribes inbound speech via OpenAkita's existing
``STTClient``, synthesizes agent replies with Edge TTS, and replays them
into the same room. It conforms to OpenAkita's ``ChannelAdapter`` ABC
so the gateway treats it exactly like Feishu/Telegram/DingTalk — same
session routing, same DoubleTextingPolicy, same tracing.
"""

from . import tts
from .channel import LiveKitChannel, LiveKitConfig
from .registry import _create_livekit, register_livekit_adapter

__all__ = [
    "LiveKitChannel",
    "LiveKitConfig",
    "register_livekit_adapter",
    "tts",
]