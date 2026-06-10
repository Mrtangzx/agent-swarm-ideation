"""Tests for the LiveKit AgentWorker.

We don't run a real LiveKit room here — the worker is exercised through
its public coroutines (``_drain_audio_source``, ``_capture_participant_audio``)
with mocked LiveKit primitives.
"""
from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.channels.livekit import LiveKitChannel, LiveKitConfig
from openakita.channels.livekit.agent_worker import (
    _capture_participant_audio,
    _drain_audio_source,
    _make_access_token,
)


def _creds() -> LiveKitConfig:
    return LiveKitConfig(
        url="ws://localhost:7880",
        api_key="devkey",
        api_secret="devsecret-change-me",
        room="voice-room",
        tts_voice="v",
    )


def _wav_bytes(duration_s: float = 0.5, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(sample_rate * duration_s))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Access token
# ---------------------------------------------------------------------------


def test_make_access_token_returns_jwt():
    token = _make_access_token("devkey", "devsecret-change-me", "agent-1", "voice-room")
    assert isinstance(token, str)
    assert token.count(".") == 2  # JWT has 3 parts separated by dots


# ---------------------------------------------------------------------------
# Audio publishing
# ---------------------------------------------------------------------------


async def test_drain_publishes_wav_to_audio_source(tmp_path: Path):
    wav_path = tmp_path / "hello.wav"
    wav_path.write_bytes(_wav_bytes(duration_s=0.2))

    captured_frames: list = []
    audio_source = MagicMock()
    audio_source.capture_frame = AsyncMock(side_effect=lambda f: captured_frames.append(f))

    q: asyncio.Queue = asyncio.Queue()
    await q.put(str(wav_path))
    await q.put(None)  # poison pill so we can break out

    stop = asyncio.create_task(asyncio.sleep(0.5))
    task = asyncio.create_task(_drain_audio_source(audio_source, q))
    # Wait long enough for the queue to drain, then cancel.
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    stop.cancel()

    assert len(captured_frames) > 0
    frame = captured_frames[0]
    assert frame.sample_rate == 24000


async def test_drain_skips_missing_file(tmp_path: Path):
    audio_source = MagicMock()
    audio_source.capture_frame = AsyncMock()

    q: asyncio.Queue = asyncio.Queue()
    await q.put(str(tmp_path / "ghost.wav"))

    task = asyncio.create_task(_drain_audio_source(audio_source, q))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    audio_source.capture_frame.assert_not_called()


# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------


async def test_capture_participant_audio_submits_to_channel_after_buffer_fills():
    """The capture loop buffers audio and submits it to _on_audio_frame
    once it crosses the ~1s threshold.
    """
    channel = LiveKitChannel(creds=_creds())
    channel._running = True

    submitted: list[dict] = []

    async def fake_submit(*, user_id, channel_user_id, chat_id, audio_bytes, sample_rate):
        submitted.append({"user_id": user_id, "size": len(audio_bytes), "rate": sample_rate})

    channel._on_audio_frame = fake_submit

    # Build a fake AudioStream that yields 5 frames of 0.4s each at 16kHz
    # = 2.0s total, which is over the 1s threshold.
    from livekit.rtc import AudioFrame

    frames = []
    for _ in range(5):
        frames_per_chunk = 16000 * 2 // 10  # 100ms of 16kHz 16-bit = 3200 frames
        f = AudioFrame(
            data=b"\x00\x00" * frames_per_chunk,
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=frames_per_chunk,
        )
        frames.append(f)

    class FakeStream:
        def __init__(self, frames): self.frames = frames
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.frames:
                raise StopAsyncIteration
            return MagicMock(frame=self.frames.pop(0))

    participant = MagicMock()
    participant.identity = "user-42"
    track = MagicMock()

    with patch_stream(frames):
        await _capture_participant_audio(track, participant, channel)

    # We expect at least one submit (likely two — first 1.0s, second 1.0s).
    assert len(submitted) >= 1
    assert submitted[0]["user_id"] == "user-42"
    assert submitted[0]["size"] >= 16000 * 2  # at least 1s worth


def patch_stream(frames):
    """Context manager that replaces livekit.rtc.AudioStream with a fake yielding the given frames."""
    from contextlib import contextmanager
    from unittest.mock import patch

    class FakeStreamCls:
        def __init__(self, _track): self._frames = list(frames)
        def __aiter__(self): return self
        async def __anext__(self):
            if not self._frames:
                raise StopAsyncIteration
            return MagicMock(frame=self._frames.pop(0))

    @contextmanager
    def _ctx():
        with patch("livekit.rtc.AudioStream", FakeStreamCls):
            yield

    return _ctx()