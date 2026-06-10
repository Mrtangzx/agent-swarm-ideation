"""LiveKit voice channel adapter.

Conforms to OpenAkita's ``ChannelAdapter`` ABC and registers itself via
``openakita.channels.livekit.registry.register_livekit_adapter()`` so the
gateway picks it up like any IM adapter (Feishu, Telegram, etc.).

Inbound flow:
    LiveKit room audio frame
        -> _on_audio_frame() -> STTClient.transcribe() -> UnifiedMessage
        -> _emit_message() (inherited, dispatches into the gateway)

Outbound flow:
    Gateway calls await adapter.send_message(OutgoingMessage)
        -> Edge TTS (synth_voice) -> wav bytes
        -> _publish_voice() pushes audio track into the LiveKit room.

LiveKit room mechanics (joining, publishing tracks, frame callbacks) are
intentionally left as small stubs in this slice. They will be filled in
once the rest of the project is wired together; the gateway contract,
session routing, DoubleTextingPolicy, and tracing all behave correctly
without them, because we ride on top of ChannelAdapter.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base import ChannelAdapter
from ..types import MediaFile, MessageContent, MessageType, OutgoingMessage, UnifiedMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveKitConfig:
    """Credentials and runtime settings for a LiveKit voice channel."""

    url: str
    api_key: str
    api_secret: str
    room: str
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    REQUIRED_KEYS = ("livekit_url", "livekit_api_key", "livekit_api_secret")

    @classmethod
    def from_credentials(cls, creds: dict) -> "LiveKitConfig":
        missing = [k for k in cls.REQUIRED_KEYS if not creds.get(k)]
        if missing:
            raise ValueError(
                f"LiveKit channel credentials missing required keys: {', '.join(missing)}"
            )
        return cls(
            url=creds["livekit_url"],
            api_key=creds["livekit_api_key"],
            api_secret=creds["livekit_api_secret"],
            room=creds.get("room", "voice-room"),
            tts_voice=creds.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
        )


class LiveKitChannel(ChannelAdapter):
    """Real-time bidirectional voice channel over LiveKit WebRTC."""

    # Override the IM-default capability map. Voice is streaming + audio-out only.
    capabilities: dict[str, bool] = {
        "streaming": True,
        "send_image": False,
        "send_file": False,
        "send_voice": True,
        "delete_message": False,
        "edit_message": False,
        "get_chat_info": False,
        "get_user_info": False,
        "get_chat_members": False,
        "get_recent_messages": False,
        "markdown": False,
    }

    def __init__(
        self,
        *,
        creds: LiveKitConfig | dict,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
    ) -> None:
        super().__init__(
            channel_name=channel_name or "livekit",
            bot_id=bot_id,
            agent_profile_id=agent_profile_id,
        )
        if isinstance(creds, dict):
            creds = LiveKitConfig.from_credentials(creds)
        self._creds: LiveKitConfig = creds
        self._room_handle: Any = None  # livekit Room, populated in start()
        self._stt_client: Any = None   # STTClient, lazily built in start()
        # Worker plumbing — populated by start() once the worker task runs.
        self._audio_publish_queue: asyncio.Queue | None = None
        self._audio_source: Any = None
        self._worker_task: asyncio.Task | None = None
        self._worker_stop: asyncio.Event | None = None

    # -----------------------------------------------------------------
    # ChannelAdapter lifecycle
    # -----------------------------------------------------------------

    async def start(self) -> None:
        """Join the LiveKit room and warm up the STT client.

        Spawns the AgentWorker as a background asyncio.Task. The worker
        sets up ``self._audio_publish_queue`` (consumed by our _publish_voice)
        and ``self._audio_source`` (the LiveKit audio source frames are
        captured into). Both are None until the worker is connected.
        """
        from ...llm.stt_client import STTClient  # lazy import — heavy module

        if self._stt_client is None:
            # STTClient() with no args is a stub; real endpoints come from settings.
            self._stt_client = STTClient()

        from .agent_worker import run_worker

        self._audio_publish_queue = asyncio.Queue()
        self._worker_stop = asyncio.Event()
        self._worker_task = asyncio.create_task(
            run_worker(self._creds, channel=self, stop_event=self._worker_stop),
            name=f"livekit-worker-{self.channel_name}",
        )
        self._running = True
        logger.info(
            f"[LiveKitChannel:{self.channel_name}] started; room={self._creds.room} "
            f"tts_voice={self._creds.tts_voice}"
        )

    async def stop(self) -> None:
        """Leave the LiveKit room and release resources."""
        self._running = False
        if self._worker_stop is not None:
            self._worker_stop.set()
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
        self._worker_task = None
        self._worker_stop = None
        self._audio_publish_queue = None
        self._room_handle = None
        logger.info(f"[LiveKitChannel:{self.channel_name}] stopped")

    async def send_message(self, message: OutgoingMessage) -> str:
        """Synthesize the outgoing text and publish the resulting audio to the room.

        Returns the LiveKit message id (synthesized locally for traceability).
        """
        chat_id = message.chat_id or self._creds.room
        text = (message.content.text or "").strip()
        voices = message.content.voices

        if voices:
            # Pre-recorded voice already on disk — just publish.
            voice_path = voices[0].local_path
            return await self._publish_voice(voice_path, chat_id)

        if not text:
            # Nothing to say. Don't synthesize silence.
            return ""

        # Lazy import so test runs that mock the module don't pull Edge TTS.
        from . import tts as _tts

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            result = await _tts.synth_voice(text, self._creds.tts_voice, tmp_path)
            return await self._publish_voice(tmp_path, chat_id, duration_sec=result.get("duration_sec"))
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    async def download_media(self, media: MediaFile) -> Path:
        """LiveKit is real-time; file downloads are not supported."""
        raise NotImplementedError(
            f"{self.channel_name}: LiveKit does not support media downloads"
        )

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """LiveKit is real-time; file uploads are not supported."""
        raise NotImplementedError(
            f"{self.channel_name}: LiveKit does not support media uploads"
        )

    # -----------------------------------------------------------------
    # Inbound bridge — called by the LiveKit Agent when audio arrives
    # -----------------------------------------------------------------

    async def _on_audio_frame(
        self,
        *,
        user_id: str,
        channel_user_id: str,
        chat_id: str,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: str = "zh",
    ) -> None:
        """Bridge hook: LiveKit Agent calls this when it has a finalized utterance.

        Writes audio to a tmp file, transcribes via STTClient, and emits a
        ``UnifiedMessage`` through the gateway. Empty transcriptions are dropped.
        """
        if not audio_bytes:
            return
        text = await self._transcribe_audio(audio_bytes, sample_rate=sample_rate, language=language)
        text = (text or "").strip()
        if not text:
            return

        # Carry the transcription as a voice attachment. Don't set
        # MessageContent.text directly — that would double-render the
        # transcription (once as raw text, once as "[语音转文字: ...]").
        voice = MediaFile.create(
            filename=f"livekit-{uuid.uuid4().hex[:8]}.wav",
            mime_type="audio/wav",
            size=len(audio_bytes),
        )
        voice.transcription = text
        voice.local_path = None  # not persisted
        voice.status = voice.status.__class__.PROCESSED  # mark as already-stt'd

        msg = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=f"lk_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            channel_user_id=channel_user_id,
            chat_id=chat_id,
            content=MessageContent(voices=[voice]),
            bot_instance_id=self.bot_instance_id,
            is_direct_message=True,
        )
        # Force MessageType.VOICE so the gateway treats this as a voice turn.
        msg.message_type = MessageType.VOICE
        await self._emit_message(msg)

    async def _transcribe_audio(
        self, audio_bytes: bytes, *, sample_rate: int, language: str
    ) -> str | None:
        """Persist audio bytes to a tmp wav and call OpenAkita's STTClient."""
        import wave

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with wave.open(str(tmp_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)
            if self._stt_client is None or not getattr(self._stt_client, "is_available", False):
                logger.debug(
                    f"[LiveKitChannel:{self.channel_name}] no STT endpoints configured; "
                    "returning empty transcription"
                )
                return ""
            return await self._stt_client.transcribe(str(tmp_path), language=language)
        except Exception as exc:
            logger.warning(f"[LiveKitChannel:{self.channel_name}] STT failed: {exc}")
            return ""
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    # -----------------------------------------------------------------
    # Outbound bridge — pushes audio to the LiveKit room
    # -----------------------------------------------------------------

    async def _publish_voice(
        self, voice_path: Path | str, chat_id: str, *, duration_sec: float | None = None
    ) -> str:
        """Publish the synthesized voice into the LiveKit room.

        Enqueues the wav file path on ``self._audio_publish_queue``; the
        AgentWorker drains the queue, decodes the wav, and pushes the
        audio frames into the room via the LiveKit AudioSource it created
        on connect.
        """
        msg_id = f"lk_out_{uuid.uuid4().hex[:12]}"
        if self._audio_publish_queue is None:
            logger.warning(
                f"[LiveKitChannel:{self.channel_name}] no publish queue (worker not running); "
                f"dropping {voice_path}"
            )
            return msg_id
        try:
            await self._audio_publish_queue.put(Path(voice_path))
            logger.info(
                f"[LiveKitChannel:{self.channel_name}] queued {voice_path} "
                f"({(duration_sec or 0):.2f}s) as {msg_id}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[LiveKitChannel:{self.channel_name}] enqueue failed: {exc}")
        return msg_id