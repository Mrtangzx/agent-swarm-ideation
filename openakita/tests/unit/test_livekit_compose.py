"""Tests for the LiveKit service in docker-compose.yml.

The voice channel adapter expects a LiveKit server reachable at ws://localhost:7880.
This test pins the docker-compose contract so a refactor doesn't silently break it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # PyYAML; already a project dep


COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_livekit_service_is_defined():
    services = _load_compose().get("services", {})
    assert "livekit" in services, "LiveKit service missing from docker-compose.yml"


def test_livekit_service_uses_official_image():
    services = _load_compose()["services"]
    image = services["livekit"].get("image", "")
    assert image.startswith("livekit/livekit-server"), f"Unexpected LiveKit image: {image!r}"


def test_livekit_service_exposes_signaling_and_rtc_ports():
    services = _load_compose()["services"]
    ports = [str(p) for p in services["livekit"].get("ports", [])]
    # HTTP signaling on 7880, TCP RTC on 7881, UDP RTC on 7882
    assert any("7880" in p for p in ports), f"Missing signaling port 7880 in {ports}"
    assert any("7881" in p for p in ports), f"Missing TCP RTC port 7881 in {ports}"
    assert any("7882" in p and "udp" in p for p in ports), (
        f"Missing UDP RTC port 7882 in {ports}"
    )


def test_openakita_service_still_defined():
    """Adding LiveKit must not displace the existing openakita service."""
    services = _load_compose()["services"]
    assert "openakita" in services, "Original openakita service disappeared"