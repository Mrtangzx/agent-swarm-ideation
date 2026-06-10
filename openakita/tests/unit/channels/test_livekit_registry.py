"""Tests for LiveKit channel registration in the adapter registry."""
from __future__ import annotations

import inspect

import pytest


def _creds() -> dict:
    return {
        "livekit_url": "ws://localhost:7880",
        "livekit_api_key": "devkey",
        "livekit_api_secret": "devsecret-change-me",
        "room": "voice-room",
        "tts_voice": "zh-CN-XiaoxiaoNeural",
    }


def test_register_livekit_adapter_adds_entry_to_registry():
    """After register_livekit_adapter(), ADAPTER_REGISTRY must contain 'livekit'."""
    from openakita.channels.livekit import register_livekit_adapter
    from openakita.channels.registry import ADAPTER_REGISTRY

    register_livekit_adapter(owner="test-livekit")
    try:
        assert "livekit" in ADAPTER_REGISTRY
    finally:
        # Clean up so we don't pollute the registry for other tests.
        from openakita.channels.registry import unregister_adapter
        unregister_adapter("livekit", owner="test-livekit")


def test_factory_signature_matches_other_adapter_factories():
    """The factory signature must mirror _create_feishu/_create_telegram so
    the gateway in main.py can call it via _create_bot_adapter(...)
    without special-casing.
    """
    from openakita.channels.livekit import _create_livekit
    from openakita.channels.registry import _create_feishu, _create_telegram

    expected_params = {"creds", "channel_name", "bot_id", "agent_profile_id"}

    for factory in (_create_livekit, _create_feishu, _create_telegram):
        sig = inspect.signature(factory)
        params = set(sig.parameters.keys())
        missing = expected_params - params
        assert not missing, f"{factory.__name__} missing params: {missing}"


def test_factory_produces_valid_livekit_channel():
    """_create_livekit must return a fully-initialized LiveKitChannel."""
    from openakita.channels.livekit import LiveKitChannel, _create_livekit

    ch = _create_livekit(
        _creds(),
        channel_name="livekit",
        bot_id="bot-1",
        agent_profile_id="default",
    )
    assert isinstance(ch, LiveKitChannel)
    assert ch.channel_name == "livekit"
    assert ch.bot_id == "bot-1"
    assert ch._creds.url == "ws://localhost:7880"
    assert ch._creds.tts_voice == "zh-CN-XiaoxiaoNeural"


def test_factory_uses_channel_name_in_bot_instance_id():
    """channel_name='livekit' should propagate to bot_instance_id for session isolation."""
    from openakita.channels.livekit import _create_livekit

    ch = _create_livekit(_creds(), channel_name="livekit", bot_id="bot-1", agent_profile_id="default")
    assert "livekit" in ch.bot_instance_id


def test_register_is_idempotent():
    """Calling register twice should not warn or duplicate."""
    from openakita.channels.livekit import register_livekit_adapter
    from openakita.channels.registry import ADAPTER_REGISTRY, _ADAPTER_OWNERS

    register_livekit_adapter(owner="test-idem")
    register_livekit_adapter(owner="test-idem")
    try:
        assert _ADAPTER_OWNERS.get("livekit") == "test-idem"
        assert ADAPTER_REGISTRY["livekit"] is not None
    finally:
        from openakita.channels.registry import unregister_adapter
        unregister_adapter("livekit", owner="test-idem")