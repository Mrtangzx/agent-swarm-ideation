"""Tests for the LiveKit voice channel adapter."""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.channels.livekit import LiveKitChannel, LiveKitConfig


# ---------------------------------------------------------------------------
# Class / configuration shape
# ---------------------------------------------------------------------------


def test_livekit_channel_extends_channel_adapter():
    """The channel must subclass OpenAkita's ChannelAdapter ABC."""
    from openakita.channels.base import ChannelAdapter

    assert issubclass(LiveKitChannel, ChannelAdapter)


def test_livekit_channel_uses_distinct_channel_name():
    """Channel name 'livekit' must not collide with IM adapters."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
        channel_name="livekit",
        bot_id="b1",
        agent_profile_id="default",
    )
    assert ch.channel_name == "livekit"


def test_livekit_channel_advertises_voice_capabilities():
    """Capabilities must include streaming + send_voice so the gateway routes correctly."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
    )
    assert ch.has_capability("streaming") is True
    assert ch.has_capability("send_voice") is True


def test_livekit_config_loaded_from_credentials_dict():
    creds = {
        "livekit_url": "ws://localhost:7880",
        "livekit_api_key": "devkey",
        "livekit_api_secret": "devsecret-change-me",
        "room": "voice-room",
        "tts_voice": "zh-CN-XiaoxiaoNeural",
    }
    cfg = LiveKitConfig.from_credentials(creds)
    assert cfg.url == "ws://localhost:7880"
    assert cfg.api_key == "devkey"
    assert cfg.room == "voice-room"
    assert cfg.tts_voice == "zh-CN-XiaoxiaoNeural"


def test_livekit_config_rejects_missing_url():
    with pytest.raises(ValueError, match="livekit_url"):
        LiveKitConfig.from_credentials({"livekit_api_key": "k", "livekit_api_secret": "s"})


# ---------------------------------------------------------------------------
# Outbound: send_message -> Edge TTS
# ---------------------------------------------------------------------------


def test_send_message_synthesizes_text_to_voice_file(tmp_path: Path):
    """send_message with text content must call Edge TTS and return a wav path."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
    )
    out_msg = MagicMock()
    out_msg.chat_id = "voice-room"
    out_msg.content.text = "你好"
    out_msg.content.voices = []  # text-only content

    fake_result = {"bytes": b"RIFF\x00\x00\x00\x00WAVEfmt ", "duration_sec": 0.5}
    with patch("openakita.channels.livekit.tts.synth_voice", new=AsyncMock(return_value=fake_result)) as synth, \
         patch.object(ch, "_publish_voice", new=AsyncMock(return_value="msg_123")) as publish:
        result = asyncio.run(ch.send_message(out_msg))

    assert result == "msg_123"
    assert synth.await_count == 1
    publish.assert_awaited_once()


def test_send_message_voice_only_content_uses_supplied_path(tmp_path: Path):
    """When the outgoing message already has a voice attachment, synthesize is skipped."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
    )
    voice_path = tmp_path / "agent.wav"
    voice_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    voice = MagicMock(local_path=str(voice_path))
    out_msg = MagicMock()
    out_msg.chat_id = "voice-room"
    out_msg.content.text = None
    out_msg.content.voices = [voice]

    with patch("openakita.channels.livekit.tts.synth_voice", new=AsyncMock()) as synth, \
         patch.object(ch, "_publish_voice", new=AsyncMock(return_value="msg_v")) as publish:
        result = asyncio.run(ch.send_message(out_msg))

    synth.assert_not_awaited()  # already has voice
    publish.assert_awaited_once()
    assert result == "msg_v"


# ---------------------------------------------------------------------------
# Inbound: audio -> STT -> emit_message
# ---------------------------------------------------------------------------


def test_inbound_audio_triggers_stt_and_emits_message():
    """An inbound audio frame must be transcribed and emitted as a UnifiedMessage."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
    )
    audio_bytes = b"\x00\x01" * 1600  # 1s of 8kHz 16-bit silence

    with patch.object(ch, "_transcribe_audio", new=AsyncMock(return_value="你好世界")) as transcribe, \
         patch.object(ch, "_emit_message", new=AsyncMock()) as emit:
        asyncio.run(ch._on_audio_frame(
            user_id="user_42",
            channel_user_id="user_42",
            chat_id="voice-room",
            audio_bytes=audio_bytes,
            sample_rate=16000,
        ))

    transcribe.assert_awaited_once()
    emit.assert_awaited_once()
    emitted_msg = emit.await_args.args[0]
    assert emitted_msg.channel == "livekit"
    assert emitted_msg.user_id == "user_42"
    assert emitted_msg.chat_id == "voice-room"
    assert "你好世界" in emitted_msg.plain_text


def test_inbound_audio_with_empty_transcription_does_not_emit():
    """If STT returns empty, do not emit a message (avoids spurious turns)."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
    )
    with patch.object(ch, "_transcribe_audio", new=AsyncMock(return_value="")) as transcribe, \
         patch.object(ch, "_emit_message", new=AsyncMock()) as emit:
        asyncio.run(ch._on_audio_frame(
            user_id="u", channel_user_id="u", chat_id="c",
            audio_bytes=b"\x00" * 1600,  # 100ms of silence, but STT returns nothing
            sample_rate=16000,
        ))

    transcribe.assert_awaited_once()
    emit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Adapter lifecycle
# ---------------------------------------------------------------------------


def test_livekit_channel_has_lifecycle_methods():
    """start/stop/send_message must exist and be callable."""
    ch = LiveKitChannel(
        creds=LiveKitConfig(url="ws://x", api_key="k", api_secret="s", room="r", tts_voice="v"),
    )
    for name in ("start", "stop", "send_message", "download_media", "upload_media"):
        method = getattr(ch, name, None)
        assert method is not None, f"missing method: {name}"
        assert callable(method), f"not callable: {name}"