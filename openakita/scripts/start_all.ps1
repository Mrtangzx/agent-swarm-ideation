# start_all.ps1 — boot the full voice demo on Windows.
#
# What it does:
#   1. Starts the LiveKit server on :7880 (foreground until you Ctrl-C;
#      this script blocks until the LiveKit process exits).
#
# For a fully automated run you'd launch OpenAkita serve in a second
# terminal — see scripts/start_voice.py.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location (Resolve-Path "$here\..")

if (-not (Test-Path "livekit/bin/livekit-server.exe")) {
    Write-Host "❌ livekit-server.exe missing. Run:" -ForegroundColor Red
    Write-Host "   curl -L -o livekit/bin/livekit.zip https://gh-proxy.com/https://github.com/livekit/livekit/releases/download/v1.13.1/livekit_1.13.1_windows_amd64.zip" -ForegroundColor Yellow
    Write-Host "   cd livekit/bin && unzip livekit.zip && del livekit.zip" -ForegroundColor Yellow
    exit 1
}

Write-Host "▶ LiveKit server starting..." -ForegroundColor Green
& ".\livekit\bin\livekit-server.exe" --config ".\livekit\livekit.yaml" --dev --bind 0.0.0.0