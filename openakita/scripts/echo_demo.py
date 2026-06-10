"""Standalone echo demo: bypasses OpenAkita's brain, proves the LiveKit <-> STT <-> TTS round-trip.

This is the simplest path to a working demo:

  user speaks Chinese in browser
    → LiveKit server
    → echo_demo subscribes to their mic
    → STT (OpenAkita's STTClient) transcribes
    → Edge TTS synthesizes "你说的是:{text}"
    → echo_demo publishes back as audio track
    → user hears reply in browser

It does NOT exercise OpenAkita's agent / brain / skills / tracing —
that's a separate step (run ``openakita serve`` and use the real bot).

Usage:
    1. Start LiveKit server:  bash scripts/start_all.ps1
       (or run livekit-server.exe directly with --config livekit/livekit.yaml --dev)
    2. Start this demo:       uv run python scripts/echo_demo.py
    3. Open test page:        http://localhost:8080/   (after running scripts/serve_test_page.py)
       Mint a token:           uv run python scripts/mint_livekit_token.py
       Paste it into the page, click 连接.
    4. Speak Chinese — the demo replies with "你说的是:<你的话>" via Edge TTS.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openakita.channels.livekit.agent_worker import _make_access_token  # noqa: E402
from openakita.llm.stt_client import STTClient  # noqa: E402
from openakita.channels.livekit.tts import synth_voice  # noqa: E402

logger = logging.getLogger("echo-demo")


def _wav_bytes_to_pcm16k(wav_bytes: bytes) -> tuple[bytes, int]:
    """Return (pcm_bytes_16khz_mono, sample_rate). For now we just unpack the existing wav."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if ch != 1 or sw != 2:
        # simplest: leave it; STT accepts arbitrary format
        pass
    return raw, sr


async def _publish_wav(audio_source, wav_path: Path) -> None:
    from livekit.rtc import AudioFrame
    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()
        samples = wf.getnframes()
        chunk = int(sample_rate * 0.02)  # 20ms
        wf.rewind()
        while True:
            data = wf.readframes(chunk)
            if not data:
                break
            frame = AudioFrame(
                data=data,
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=len(data) // 2,
            )
            await audio_source.capture_frame(frame)
    logger.info(f"[echo] published {samples / sample_rate:.1f}s of audio to room")


async def _capture_and_respond(track, participant, stt, audio_source, room_name) -> None:
    from livekit.rtc import AudioStream
    stream = AudioStream(track)
    buf = bytearray()
    target = 16000 * 2 * 2  # 2s of 16kHz 16-bit
    sample_rate = 16000

    async for event in stream:
        if event.frame is None:
            continue
        sample_rate = event.frame.sample_rate
        buf.extend(event.frame.data)
        if len(buf) < target:
            continue
        audio_bytes = bytes(buf)
        buf.clear()

        # Save to a temp wav and run STT.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with wave.open(str(tmp_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)
            logger.info(f"[echo] captured {len(audio_bytes)} bytes from {participant.identity}, sending to STT")
            text = await stt.transcribe(str(tmp_path), language="zh")
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        text = (text or "").strip()
        if not text:
            logger.info("[echo] STT returned empty, skipping")
            continue

        logger.info(f"[echo] transcribed: {text!r}")

        # Synthesize a reply.
        reply_text = f"你说的是:{text}"
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            reply_path = Path(tmp.name)
        try:
            await synth_voice(reply_text, "zh-CN-XiaoxiaoNeural", reply_path)
            logger.info(f"[echo] synthesized {reply_path.stat().st_size} bytes; publishing to room {room_name}")
            await _publish_wav(audio_source, reply_path)
        finally:
            try:
                reply_path.unlink()
            except OSError:
                pass


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    url = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
    api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "devsecret-change-me")
    room = os.getenv("LIVEKIT_ROOM", "voice-room")

    from livekit.rtc import Room, AudioSource, LocalAudioTrack, TrackSource as RtcTrackSource

    stt = STTClient()
    if not stt.is_available:
        logger.warning("⚠ STTClient has no endpoints configured — STT calls will return empty.")
        logger.warning("  Configure endpoints via OpenAkita settings to enable real transcription.")
    else:
        logger.info(f"STT endpoints: {[ep.name for ep in stt.endpoints]}")

    rtc_room = Room()
    audio_source = AudioSource(sample_rate=24000, num_channels=1)

    token = _make_access_token(api_key=api_key, api_secret=api_secret, identity="echo-demo", room=room)

    @rtc_room.on("track_subscribed")
    def _on_track(track, pub, participant):
        if track.kind != "audio":
            return
        logger.info(f"[echo] subscribed to {participant.identity}; capturing...")
        asyncio.create_task(_capture_and_respond(track, participant, stt, audio_source, room))

    @rtc_room.on("participant_connected")
    def _on_pc(p):
        logger.info(f"[echo] participant joined: {p.identity}")

    await rtc_room.connect(url, token)
    logger.info(f"[echo] connected to {url} (room={room})")

    track = LocalAudioTrack.create_audio_track("echo-reply", audio_source)
    await rtc_room.local_participant.publish_track(track)
    logger.info("[echo] published echo-reply audio track")

    try:
        # Run forever.
        await asyncio.Event().wait()
    finally:
        await rtc_room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())