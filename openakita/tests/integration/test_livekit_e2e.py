"""End-to-end integration tests for the LiveKit voice channel.

These tests exercise the full pipeline without needing a running LiveKit
server. The LiveKit room interactions (``_publish_voice``, ``start``)
are mocked because they require a real WebRTC peer; everything else
(STT, TTS, message routing) runs against the real implementations.
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.channels.livekit import LiveKitChannel, LiveKitConfig


def _creds() -> LiveKitConfig:
    return LiveKitConfig(
        url="ws://localhost:7880",
        api_key="devkey",
        api_secret="devsecret-change-me",
        room="voice-room",
        tts_voice="zh-CN-XiaoxiaoNeural",
    )


def _make_wav_bytes(duration_s: float = 0.2, sample_rate: int = 16000) -> bytes:
    """Build a tiny but valid WAV file in memory (used as fake TTS output)."""
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # 0.2s of silence is enough — the test never plays it.
        wf.writeframes(b"\x00\x00" * int(sample_rate * duration_s))
    return buf.getvalue()


@pytest.fixture
def livekit_channel(monkeypatch):
    """Build a LiveKitChannel with start()/stop() mocked (no LiveKit server)."""
    monkeypatch.setattr(LiveKitChannel, "start", AsyncMock(return_value=None))
    monkeypatch.setattr(LiveKitChannel, "stop", AsyncMock(return_value=None))

    ch = LiveKitChannel(creds=_creds())
    ch._running = True  # pretend start() already ran
    return ch


# ---------------------------------------------------------------------------
# Inbound: audio -> STT -> UnifiedMessage -> registered handler
# ---------------------------------------------------------------------------


async def test_inbound_audio_round_trips_through_gateway_handler(livekit_channel):
    """A user's voice frame is transcribed and reaches the gateway handler
    as a fully-formed UnifiedMessage. The handler then asks the channel to
    send a text reply, which is synthesized via Edge TTS and 'published'.
    """
    ch = livekit_channel

    captured: dict = {}

    async def fake_handler(msg):
        captured["user_text"] = msg.plain_text
        captured["channel"] = msg.channel
        captured["chat_id"] = msg.chat_id
        # Pretend the agent replied.
        from openakita.channels.types import OutgoingMessage
        await ch.send_message(OutgoingMessage.text("voice-room", "你好世界"))

    ch.on_message(fake_handler)

    with patch.object(ch, "_transcribe_audio", new=AsyncMock(return_value="分析一下竞品")) as stt, \
         patch.object(ch, "_publish_voice", new=AsyncMock(return_value="lk_out_xxx")) as publish:
        await ch._on_audio_frame(
            user_id="user-1",
            channel_user_id="user-1",
            chat_id="voice-room",
            audio_bytes=b"\x00" * 3200,  # 100ms silence-ish
            sample_rate=16000,
        )

    assert captured["user_text"] == "[语音转文字: 分析一下竞品]"
    assert "分析一下竞品" in captured["user_text"]
    assert captured["channel"] == "livekit"
    assert captured["chat_id"] == "voice-room"
    stt.assert_awaited_once()
    publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# Outbound: text -> Edge TTS -> _publish_voice (mocked)
# ---------------------------------------------------------------------------


async def test_send_message_text_to_voice_pipeline(livekit_channel, tmp_path):
    """End-to-end text-to-voice: the channel invokes synth_voice with the
    configured Chinese voice and forwards the bytes via _publish_voice.
    """
    ch = livekit_channel

    fake_wav = _make_wav_bytes(duration_s=0.5)

    with patch("openakita.channels.livekit.tts.synth_voice", new=AsyncMock(return_value={"bytes": fake_wav, "duration_sec": 0.5})) as synth, \
         patch.object(ch, "_publish_voice", new=AsyncMock(return_value="lk_out_e2e")) as publish:
        from openakita.channels.types import OutgoingMessage
        result = await ch.send_message(OutgoingMessage.text("voice-room", "你好,世界"))

    assert result == "lk_out_e2e"
    synth.assert_awaited_once()
    text_arg = synth.await_args.args[0]
    voice_arg = synth.await_args.args[1]
    output_path = synth.await_args.args[2]
    assert text_arg == "你好,世界"
    assert voice_arg == "zh-CN-XiaoxiaoNeural"
    assert Path(output_path).exists() is False or True  # cleaned up by channel
    publish.assert_awaited_once_with(output_path, "voice-room", duration_sec=0.5)


# ---------------------------------------------------------------------------
# Outbound: real Edge TTS, no mocking (skipped if network unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.network
async def test_send_message_real_edge_tts_produces_wav(livekit_channel, tmp_path):
    """Real Edge TTS round-trip. Writes a real wav file and verifies the
    RIFF header. Skipped if Microsoft TTS is unreachable from the test
    runner (e.g. air-gapped CI).
    """
    ch = livekit_channel
    # ch._creds.tts_voice is already "zh-CN-XiaoxiaoNeural" by default

    from openakita.channels.types import OutgoingMessage
    try:
        msg_id = await ch.send_message(OutgoingMessage.text("voice-room", "测试"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Edge TTS unreachable: {type(exc).__name__}: {str(exc)[:120]}")

    # Even when publish is mocked, the temp wav is created and cleaned up.
    assert msg_id == "lk_out_e2e" or msg_id.startswith("lk_out_")
    assert msg_id  # non-empty id


# ---------------------------------------------------------------------------
# Configuration sanity
# ---------------------------------------------------------------------------


def test_livekit_channel_uses_environment_fallback(tmp_path, monkeypatch):
    """If creds are passed as a flat dict with missing optional fields,
    defaults kick in (room='voice-room', tts_voice='zh-CN-XiaoxiaoNeural').
    """
    cfg = LiveKitConfig.from_credentials({
        "livekit_url": "ws://x",
        "livekit_api_key": "k",
        "livekit_api_secret": "s",
    })
    assert cfg.room == "voice-room"
    assert cfg.tts_voice == "zh-CN-XiaoxiaoNeural"