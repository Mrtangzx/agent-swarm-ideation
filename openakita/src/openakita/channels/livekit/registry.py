"""Register the LiveKit channel adapter with OpenAkita's adapter registry.

Usage from a plugin or main.py::

    from openakita.channels.livekit import register_livekit_adapter
    register_livekit_adapter()
"""
from __future__ import annotations

import logging

from ..registry import register_adapter
from .channel import LiveKitChannel

logger = logging.getLogger(__name__)


def _create_livekit(
    creds: dict,
    *,
    channel_name: str,
    bot_id: str,
    agent_profile_id: str,
) -> LiveKitChannel:
    """Adapter factory matching the OpenAkita channel registry contract.

    Signature mirrors ``_create_feishu``/``_create_telegram`` in
    ``openakita/channels/registry.py`` — the gateway calls factories with
    exactly this shape.
    """
    return LiveKitChannel(
        creds=creds,
        channel_name=channel_name,
        bot_id=bot_id,
        agent_profile_id=agent_profile_id,
    )


def register_livekit_adapter(*, owner: str = "openakita-livekit") -> None:
    """Idempotent: registers the livekit factory if not already present."""
    register_adapter("livekit", _create_livekit, owner=owner)
    logger.info("[LiveKitChannel] registered factory under bot_type='livekit'")