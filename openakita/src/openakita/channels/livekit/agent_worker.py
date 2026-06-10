"""LiveKit AgentWorker — joins a LiveKit room and bridges audio frames.

Architecture:
    LiveKit Room (WebRTC)
        ↕ audio frames
    LiveKit AgentWorker  ← this module
        ↕ asyncio.Queue / direct calls
    LiveKitChannel (OpenAkita side)

The worker runs inside the OpenAkita process — ``LiveKitChannel.start()``
spawns it as a background task. It uses the raw ``livekit.rtc.Room`` API
rather than ``livekit-agents`` AgentSession because we don't need the
full STT→LLM→TTS pipeline that AgentSession provides; OpenAkita already
has all of that, and we just want to shuttle PCM in and out.
"""
from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path
from typing import Any

from .channel import LiveKitChannel

logger = logging.getLogger(__name__)


def _make_access_token(api_key: str, api_secret: str, identity: str, room: str, ttl_seconds: int = 3600) -> str:
    """Mint a LiveKit JWT access token for the agent.

    We need an access token (not just API key/secret) because we connect
    as a participant, not as a server admin. ``livekit.api.AccessToken``
    handles the JWT construction.
    """
    import datetime
    from livekit.api import AccessToken, VideoGrants

    grants = VideoGrants(
        room=room,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        # Allow every source. The Python SDK's create_audio_track defaults
        # the track's source to SOURCE_UNKNOWN (value 0), which the server
        # rejects if it's not in the grant list. Listing every name here
        # (including "unknown") keeps it permissive; tighten once we wire
        # explicit track sources.
        can_publish_sources=[
            "unknown", "camera", "microphone", "screen_share", "screen_share_audio",
        ],
    )
    token = AccessToken(api_key, api_secret) \
        .with_identity(identity) \
        .with_name("openakita-voice") \
        .with_grants(grants) \
        .with_ttl(datetime.timedelta(seconds=ttl_seconds))
    return token.to_jwt()


async def _drain_audio_source(audio_source: Any, queue: asyncio.Queue) -> None:
    """Pull wav file paths from the queue and publish them as LiveKit audio frames."""
    while True:
        voice_path = await queue.get()
        try:
            voice_path = Path(voice_path)
            if not voice_path.exists():
                logger.warning(f"[LiveKitWorker] publish path missing: {voice_path}")
                continue
            with wave.open(str(voice_path), "rb") as wf:
                sample_rate = wf.getframerate()
                num_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                if num_channels != 1 or sample_width != 2:
                    logger.warning(
                        f"[LiveKitWorker] unsupported wav format "
                        f"(ch={num_channels}, sw={sample_width}); skipping"
                    )
                    continue
                # Push in small chunks so the room hears continuous audio.
                chunk_frames = int(sample_rate * 0.02)  # 20ms
                while True:
                    data = wf.readframes(chunk_frames)
                    if not data:
                        break
                    from livekit.rtc import AudioFrame
                    frame = AudioFrame(
                        data=data,
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=len(data) // 2,
                    )
                    await audio_source.capture_frame(frame)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[LiveKitWorker] publish failed for {voice_path}: {exc}")
        finally:
            queue.task_done()


async def _capture_participant_audio(track: Any, participant: Any, channel: LiveKitChannel) -> None:
    """Stream audio from a remote participant into the channel for STT."""
    from livekit.rtc import AudioStream

    stream = AudioStream(track)
    user_id = participant.identity
    chat_id = channel._creds.room

    # Buffer up to ~1s of audio, then submit as one STT chunk. Without
    # a real VAD this is a coarse segmentation, but it lets us demo the
    # end-to-end pipeline without pulling in silero-vad.
    buffer = bytearray()
    buffer_target = 16000 * 2  # 1 second of 16kHz 16-bit mono = 32KB
    sample_rate = 16000

    async for event in stream:
        if event.frame is None:
            continue
        sample_rate = event.frame.sample_rate
        buffer.extend(event.frame.data)
        if len(buffer) >= buffer_target:
            audio_bytes = bytes(buffer)
            buffer.clear()
            try:
                await channel._on_audio_frame(
                    user_id=user_id,
                    channel_user_id=user_id,
                    chat_id=chat_id,
                    audio_bytes=audio_bytes,
                    sample_rate=sample_rate,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[LiveKitWorker] STT submit failed: {exc}")


async def run_worker(creds, channel: LiveKitChannel, *, stop_event: asyncio.Event) -> None:
    """Connect to the LiveKit room, subscribe to participants, expose an audio source.

    Runs forever until ``stop_event`` is set. The caller (LiveKitChannel.start)
    awaits this coroutine in a background task.
    """
    from livekit.rtc import Room, AudioSource, LocalAudioTrack

    room = Room()
    identity = f"agent-{channel.channel_name}"
    token = _make_access_token(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        identity=identity,
        room=creds.room,
    )

    # Wire callbacks BEFORE connect so we don't miss early tracks.
    publish_queue: asyncio.Queue = asyncio.Queue()

    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant):
        if track.kind != "audio":
            return
        logger.info(
            f"[LiveKitWorker] subscribed to audio from {participant.identity}"
        )
        asyncio.create_task(_capture_participant_audio(track, participant, channel))

    @room.on("participant_connected")
    def _on_participant_connected(participant):
        logger.info(f"[LiveKitWorker] participant joined: {participant.identity}")

    @room.on("disconnected")
    def _on_disconnected():
        logger.warning("[LiveKitWorker] room disconnected")

    logger.info(f"[LiveKitWorker] connecting to {creds.url} as {identity} (room={creds.room})")
    await room.connect(creds.url, token)
    logger.info(f"[LiveKitWorker] connected to room: {creds.room}")

    # Set up audio publishing — channel._publish_voice will enqueue into this queue.
    audio_source = AudioSource(sample_rate=24000, num_channels=1)
    local_track = LocalAudioTrack.create_audio_track("agent-voice", audio_source)
    await room.local_participant.publish_track(local_track)
    logger.info("[LiveKitWorker] published agent-voice track")

    channel._audio_source = audio_source
    channel._audio_publish_queue = publish_queue

    publisher_task = asyncio.create_task(_drain_audio_source(audio_source, publish_queue))

    try:
        await stop_event.wait()
    finally:
        publisher_task.cancel()
        await room.disconnect()
        logger.info("[LiveKitWorker] stopped")